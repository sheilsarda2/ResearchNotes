"""Prepare a task graph without executing it or touching broker storage."""
import copy
from dataclasses import dataclass
import pickle
import uuid

from huey.api import Task, group, chord
from huey.utils import ChordConfig


@dataclass(frozen=True)
class TaskGraphPlan:
    request: bytes
    messages: tuple
    result: dict
    task_ids: tuple


def prepare_task_graph(huey, original):
    # Validate before recursive copying/serialization so cycles fail cleanly.
    objects, task_ids = set(), []
    ids = set()

    def check(value):
        if id(value) in objects:
            raise ValueError('task graphs must be trees without shared nodes')
        objects.add(id(value))
        if isinstance(value, Task):
            if value.id in ids:
                raise ValueError('task ids must be unique within a graph')
            ids.add(value.id)
            task_ids.append(value.id)
            if value.chord_config is not None:
                raise ValueError('already-bound chord members cannot be staged')
            huey._registry.string_to_task(huey._registry.task_to_string(type(value)))
            for next_task in (value.on_complete, value.on_error):
                if next_task is not None:
                    if not isinstance(next_task, Task):
                        raise ValueError('pipeline links must be Tasks')
                    check(next_task)
        elif isinstance(value, (group, chord)):
            if not isinstance(value.tasks, (list, tuple)):
                raise ValueError('graph members must be a finite list or tuple')
            for member in value.tasks:
                if isinstance(value, chord) and isinstance(member, group):
                    raise ValueError('a chord member must be a Task or chord')
                check(member)
            if isinstance(value, chord):
                if not isinstance(value.callback, Task):
                    raise ValueError('chord callback must be a Task')
                check(value.callback)
        else:
            raise ValueError('expected a Task, group or chord')

    check(original)
    graph = copy.deepcopy(original)

    def request(value):
        if isinstance(value, Task):
            return ('task', huey._registry.create_message(value))
        if isinstance(value, chord):
            return ('chord', tuple(request(t) for t in value.tasks),
                    request(value.callback))
        return ('group', tuple(request(t) for t in value.tasks))

    # Fingerprint the logical request before resolving relative expiry or
    # assigning chord synchronization ids. Compression timestamps and signatures
    # must not make an unchanged submission conflict with itself.
    request_bytes = pickle.dumps(request(graph), protocol=4)
    messages = []

    def pipeline(task):
        results = []
        while task is not None:
            results.append({'kind': 'task', 'id': task.id})
            task = task.on_complete
        return results

    def queue(task):
        if task.expires:
            task.resolve_expires(huey.utc)
        messages.append((huey.serialize_task(task), task.priority))

    def plan(value):
        if isinstance(value, Task):
            queue(value)
            results = pipeline(value)
            return results[0] if len(results) == 1 else {
                'kind': 'group', 'results': results}
        if isinstance(value, group):
            return {'kind': 'group', 'results': [plan(t) for t in value.tasks]}

        cid, size = str(uuid.uuid4()), len(value.tasks)
        members = []
        for idx, member in enumerate(value.tasks):
            head = member.callback if isinstance(member, chord) else member
            tail = head
            while tail.on_complete is not None:
                tail = tail.on_complete
            tail.chord_config = ChordConfig(cid, size, idx, value.callback)
            members.append({'kind': 'task', 'id': tail.id})
            plan(member)
        if not size:
            # No member can trigger an empty chord; schedule its callback with
            # the same ordered-results shape that an ordinary chord provides.
            value.callback.extend_data(([],))
            queue(value.callback)
        return {'kind': 'chord', 'members': members,
                'callback': {'kind': 'task', 'id': value.callback.id},
                'pipeline': pipeline(value.callback)}

    descriptor = plan(graph)
    return TaskGraphPlan(request_bytes, tuple(messages), descriptor,
                         tuple(task_ids))
