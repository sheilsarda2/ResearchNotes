"""Importable application tasks supplied by the independent verifier."""
import json
import os
from pathlib import Path

import luigi

Base = getattr(luigi, "CheckpointTask", luigi.Task)


class Number(luigi.Task):
    root = luigi.Parameter()
    number = luigi.IntParameter()
    annotation = luigi.Parameter(default="default", significant=False)

    def output(self):
        return luigi.LocalTarget(str(Path(self.root) / f"number-{self.number}.json"))

    def run(self):
        if Path(self.root, f"fail-{self.number}").exists():
            raise RuntimeError("deliberate child failure")
        with self.output().open("w") as stream:
            json.dump({"value": self.number * 10, "annotation": self.annotation}, stream)


class Workflow(Base):
    root = luigi.Parameter()
    cohort = luigi.Parameter(default="a")
    ignored = luigi.Parameter(default="ignored", significant=False)

    def checkpoint_path(self):
        return Path(self.root) / (self.cohort + ".checkpoint.json")

    def output(self):
        return luigi.LocalTarget(str(Path(self.root) / (self.cohort + ".out")))

    def initial_state(self):
        return {"phase": 0, "sum": 0}

    def trace(self, phase):
        with open(Path(self.root) / (self.cohort + ".trace"), "a") as stream:
            stream.write(str(phase) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def advance(self, state, inputs):
        self.trace(state["phase"])
        if state["phase"] == 0:
            assert inputs is None
            child = Number(self.root, 1, annotation="retained")
            return luigi.Transition({"phase": 1, "sum": 0}, {
                "batch": [child, (Number(self.root, 2), None)], "again": child,
            })
        if state["phase"] == 1:
            assert type(inputs) is dict and type(inputs["batch"]) is list
            assert type(inputs["batch"][1]) is tuple and inputs["batch"][1][1] is None
            with inputs["batch"][0].open() as stream:
                first = json.load(stream)
            assert first["annotation"] == "retained"
            with inputs["batch"][1][0].open() as stream:
                second = json.load(stream)
            return luigi.Transition({"phase": 2, "sum": first["value"] + second["value"]},
                                    {"last": Number(self.root, 3), "empty": (), "nothing": None})
        assert state["phase"] == 2
        assert inputs["empty"] == () and inputs["nothing"] is None
        with inputs["last"].open() as stream:
            total = state["sum"] + json.load(stream)["value"]
        with self.output().open("w") as stream:
            stream.write(str(total))
        return luigi.Finish({"phase": 3, "sum": total})


class CollisionWorkflow(Workflow):
    def checkpoint_path(self):
        return Path(self.root) / "one.checkpoint.json"


class InvalidWorkflow(Workflow):
    mode = luigi.Parameter()

    def initial_state(self):
        if self.mode == "bad_initial":
            return {"data": {1, 2}}
        return super().initial_state()

    def advance(self, state, inputs):
        if self.mode == "bad_return":
            return state
        if self.mode == "missing_outputs":
            return luigi.Finish({"done": True})
        if self.mode == "hook_error":
            state["phase"] = 999
            raise LookupError("application failure")
        if self.mode == "self":
            return luigi.Transition(state, self)
        if self.mode == "req_cycle":
            cycle = []; cycle.append(cycle)
            return luigi.Transition(state, cycle)
        if self.mode == "req_leaf":
            return luigi.Transition(state, "not a task")
        if self.mode == "req_key":
            return luigi.Transition(state, {1: Number(self.root, 1)})
        if self.mode == "local_task":
            class Local(luigi.Task):
                pass
            return luigi.Transition(state, Local())
        bad = {"nan": float("nan"), "infinity": float("inf"), "bytes": b"x",
               "tuple": (1, 2), "set": {1}, "key": {1: "x"}}
        if self.mode == "state_cycle":
            value = []; value.append(value)
        else:
            value = bad[self.mode]
        return luigi.Transition(value)


class EmptyTransitions(Workflow):
    def advance(self, state, inputs):
        phase = state["phase"]
        if phase == 0:
            assert inputs is None
            return luigi.Transition({"phase": 1}, {"a": [], "b": (), "c": None})
        assert inputs == {"a": [], "b": (), "c": None}
        with self.output().open("w") as stream:
            stream.write("empty ok")
        return luigi.Finish({"phase": 2})


class BlockingWorkflow(Workflow):
    def advance(self, state, inputs):
        if state["phase"] == 0:
            print("ADVANCING", flush=True)
            input()
        return super().advance(state, inputs)


class PrivateNumber(luigi.Task):
    root = luigi.Parameter()
    token = luigi.Parameter(default="default-token", visibility=luigi.parameter.ParameterVisibility.PRIVATE)

    def output(self):
        return luigi.LocalTarget(str(Path(self.root) / "private.txt"))

    def run(self):
        with self.output().open("w") as stream:
            stream.write(self.token)


class PrivateWorkflow(Workflow):
    def advance(self, state, inputs):
        if state["phase"] == 0:
            return luigi.Transition({"phase": 1}, PrivateNumber(self.root, token="preserved-token"))
        with inputs.open() as stream:
            value = stream.read()
        assert value == "preserved-token"
        with self.output().open("w") as stream:
            stream.write(value)
        return luigi.Finish({"phase": 2})
