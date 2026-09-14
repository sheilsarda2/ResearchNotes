"""Behavioral verification of composed schema evolution, independent of its implementation."""
import hashlib
import json
import sqlite3
import subprocess
import sys

import pytest
from click.testing import CliRunner
from sqlite_utils import Database
from sqlite_utils.cli import cli
from sqlite_utils.db import TransformError


def apply(db, plan, **kwargs):
    assert callable(getattr(db, 'transform_schema', None)), 'Database.transform_schema is missing'
    return db.transform_schema(plan, **kwargs)


def state(db):
    return db.conn.serialize()


def rows(db, sql):
    return [tuple(r) for r in db.execute(sql)]


def error_unchanged(db, plan, **kwargs):
    before = state(db)
    with pytest.raises(TransformError):
        apply(db, plan, **kwargs)
    assert not db.conn.in_transaction
    assert state(db) == before
    assert not rows(db, "SELECT name FROM sqlite_master WHERE name LIKE '__schema_plan%'")


def test_composed_graph_preserves_real_behavior():
    db = Database(memory=True)
    db.executescript('''
    PRAGMA foreign_keys=ON;
    CREATE TABLE parent (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      label TEXT COLLATE NOCASE CONSTRAINT label_unique UNIQUE ON CONFLICT ABORT,
      amount TEXT DEFAULT '03', obsolete BLOB,
      CONSTRAINT label_nonempty CHECK(length(label)>0)
    );
    CREATE TABLE child (
      child_id INTEGER PRIMARY KEY, parent_id TEXT,
      weight TEXT DEFAULT '1',
      FOREIGN KEY(parent_id) REFERENCES parent(id) ON UPDATE CASCADE ON DELETE CASCADE
          DEFERRABLE INITIALLY DEFERRED
    );
    CREATE TABLE audit(kind TEXT, value TEXT);
    CREATE INDEX label_expr ON parent(lower(label) DESC) WHERE label <> 'label';
    CREATE INDEX child_partial ON child(weight COLLATE BINARY DESC) WHERE weight IS NOT NULL;
    CREATE VIEW parent_view(stable_label, value) AS SELECT label, amount FROM parent;
    CREATE VIEW nested AS SELECT stable_label AS visible, value FROM parent_view;
    CREATE VIEW joined AS SELECT p.label AS name, c.weight AS weight FROM parent p JOIN child c ON c.parent_id=p.id;
    CREATE TRIGGER parent_change AFTER UPDATE OF label ON parent
      BEGIN INSERT INTO audit VALUES('label', NEW.label || ':label'); END;
    CREATE TRIGGER child_delete AFTER DELETE ON child
      BEGIN INSERT INTO audit VALUES('delete', OLD.weight); END;
    INSERT INTO parent(id,label,amount,obsolete) VALUES(7,'Alpha','2.5',x'ff');
    INSERT INTO parent(id,label,amount) VALUES(70,'High','1');
    DELETE FROM parent WHERE id=70;
    INSERT INTO child VALUES(8,'7','04');
    ''')
    plan = {
      'PARENT': {'rename': {'ID':'parent_key','LABEL':'title'}, 'types':{'amount':'REAL'}, 'drop':['obsolete'], 'defaults':{'amount':2.5},'column_order':['amount','id'], 'not_null':{'label':True}},
      'child': {'rename': {'parent_id':'owner'}, 'types':{'parent_id':'INTEGER','weight':int},'strict':True},
    }
    before=state(db)
    preview=apply(db, plan, dry_run=True)
    assert state(db)==before
    report=apply(db, plan)
    assert report==preview
    assert report['tables']==[{'name':'child','rows':1,'columns':['child_id','owner','weight']},{'name':'parent','rows':1,'columns':['amount','parent_key','title']}]
    assert rows(db,'SELECT * FROM nested')==[('Alpha',2.5)]
    assert rows(db,'SELECT * FROM joined')==[('Alpha',4)]
    assert rows(db,'SELECT rowid,parent_key,title,amount FROM parent')==[(7,7,'Alpha',2.5)]
    assert rows(db,'SELECT * FROM child')==[(8,7,4)]
    assert rows(db,'SELECT * FROM audit')==[]
    assert rows(db,'PRAGMA foreign_key_check')==[]
    assert 'DEFERRABLE INITIALLY DEFERRED' in db['child'].schema
    assert db['child'].strict
    assert rows(db,'SELECT seq FROM sqlite_sequence WHERE name="parent"')==[(70,)]
    assert {r['name'] for r in report['schema'] if r['type']=='index'}=={'label_expr','child_partial'}
    db.execute("UPDATE parent SET title='Beta'")
    db.conn.commit()
    assert rows(db,'SELECT * FROM audit')==[('label','Beta:label')]
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO parent(title) VALUES('bETA')")
    db.conn.rollback()
    db.execute("INSERT INTO parent(title) VALUES('Gamma')")
    db.conn.commit()
    assert rows(db,"SELECT parent_key,amount FROM parent WHERE title='Gamma'")==[(71,2.5)]
    db.execute('DELETE FROM parent WHERE parent_key=7')
    db.conn.commit()
    assert rows(db,'SELECT * FROM child')==[]
    assert rows(db,"SELECT value FROM audit WHERE kind='delete'")==[('4',)]
    # The preserved FK remains initially deferred, not silently made immediate.
    db.execute('BEGIN')
    db.execute('INSERT INTO child VALUES(9,900,5)')
    db.execute("INSERT INTO parent(parent_key,title) VALUES(900,'Late')")
    db.conn.commit()


def test_simultaneous_swaps_quoted_names_and_literals():
    db=Database(memory=True)
    db.executescript('''CREATE TABLE "odd table" ("a" TEXT, "b" TEXT, "quote\"\"col" TEXT);
    INSERT INTO "odd table" VALUES('left','right','q');
    CREATE VIEW "odd view" AS SELECT a AS first, b AS second, 'a b' AS literal FROM "odd table";
    CREATE INDEX "odd index" ON "odd table"(a || 'b', b) WHERE a <> 'a';''')
    apply(db, {'odd table':{'rename':{'a':'b','b':'a','quote"col':'semi;colon'},'column_order':['b','quote"col']}})
    assert [c.name for c in db['odd table'].columns]==['a','semi;colon','b']
    assert rows(db,'SELECT * FROM "odd view"')==[('left','right','a b')]
    assert rows(db,'SELECT a,b,"semi;colon" FROM "odd table"')==[('right','left','q')]
    assert "'b'" in rows(db,"SELECT sql FROM sqlite_master WHERE name='odd index'")[0][0]


def test_incoming_fk_and_trigger_on_unplanned_table():
    db=Database(memory=True)
    db.executescript('''PRAGMA foreign_keys=ON;
    CREATE TABLE p(id INTEGER PRIMARY KEY, val TEXT);
    CREATE TABLE c(pid INTEGER REFERENCES p(id) ON UPDATE CASCADE);
    CREATE TABLE input(v TEXT);
    CREATE TRIGGER input_change AFTER INSERT ON input BEGIN UPDATE p SET val=NEW.v WHERE id=1; END;
    INSERT INTO p VALUES(1,'before'); INSERT INTO c VALUES(1);''')
    apply(db, {'p':{'rename':{'id':'key','val':'value'},'not_null':{'val':True}}})
    assert rows(db,'PRAGMA foreign_key_list(c)')[0][4]=='key'
    db.execute("INSERT INTO input VALUES('after')")
    db.execute('UPDATE p SET key=5')
    db.conn.commit()
    assert rows(db,'SELECT * FROM p')==[(5,'after')]
    assert rows(db,'SELECT * FROM c')==[(5,)]


def test_cycle_composite_fk_and_actions_preserved():
    db=Database(memory=True)
    db.executescript('''PRAGMA foreign_keys=ON;
    CREATE TABLE a(x INTEGER, y TEXT COLLATE NOCASE, b_id INTEGER REFERENCES b(id) DEFERRABLE INITIALLY DEFERRED,
       PRIMARY KEY(x,y));
    CREATE TABLE b(id INTEGER PRIMARY KEY, ax INTEGER, ay TEXT,
       FOREIGN KEY(ax,ay) REFERENCES a(x,y) ON DELETE SET NULL ON UPDATE CASCADE DEFERRABLE INITIALLY DEFERRED);
    BEGIN; INSERT INTO a(rowid,x,y,b_id) VALUES(42,1,'Case',3); INSERT INTO b VALUES(3,1,'case'); COMMIT;''')
    apply(db, {'a':{'rename':{'x':'key'},'column_order':['y']},'b':{'rename':{'ax':'ref'},'column_order':['ay']}})
    assert rows(db,'SELECT rowid,key,y,b_id FROM a')==[(42,1,'Case',3)]
    assert rows(db,'PRAGMA foreign_key_check')==[]
    db.execute('DELETE FROM a');db.conn.commit()
    assert rows(db,'SELECT ref,ay FROM b')==[(None,None)]


@pytest.mark.parametrize('definition,values,expected',[
 ('v TEXT',"INSERT INTO t(rowid,v) VALUES(-4,'x'),(42,'y')",[(-4,'x'),(42,'y')]),
 ('rowid TEXT, v TEXT',"INSERT INTO t(_rowid_,rowid,v) VALUES(61,'named','x')",[(61,'named','x')]),
 ('id INTEGER PRIMARY KEY DESC, v TEXT',"INSERT INTO t(rowid,id,v) VALUES(91,4,'x')",[(91,4,'x')]),
 ('x TEXT, y INTEGER, PRIMARY KEY(x,y)',"INSERT INTO t(rowid,x,y) VALUES(122,'a',3)",[(122,'a',3)]),
])
def test_hidden_rowids(definition,values,expected):
    db=Database(memory=True);db.execute('CREATE TABLE t('+definition+')');db.execute(values);db.conn.commit()
    apply(db, {'t':{'column_order':[]}})
    alias='_rowid_' if definition.startswith('rowid') else 'rowid'
    assert rows(db,'SELECT '+alias+',* FROM t ORDER BY '+alias)==expected


def test_empty_autoincrement_highwater_and_unrelated_objects():
    db=Database(memory=True)
    db.executescript('''CREATE TABLE t(id INTEGER PRIMARY KEY AUTOINCREMENT,v TEXT); INSERT INTO t VALUES(800,'x'); DELETE FROM t;
    CREATE TABLE __schema_plan_rebuild(keep TEXT); INSERT INTO __schema_plan_rebuild VALUES('sentinel');
    CREATE VIRTUAL TABLE unrelated USING fts5(text); INSERT INTO unrelated VALUES('searchable');''')
    apply(db, {'t':{'types':{'v':'BLOB'},'strict':True}})
    db.execute("INSERT INTO t(v) VALUES(x'78')");db.conn.commit()
    assert rows(db,'SELECT * FROM t')==[(801,b'x')]
    assert rows(db,'SELECT * FROM __schema_plan_rebuild')==[('sentinel',)]
    assert rows(db,"SELECT text FROM unrelated WHERE unrelated MATCH 'searchable'")==[('searchable',)]


@pytest.mark.parametrize('target,value,expected',[
 ('INTEGER','  +1.20e2 ',120),('integer','',None),('INTEGER',' \t ',None),('INTEGER',-2,-2),
 ('INTEGER',3.0,3),('INTEGER',str(2**63-1),2**63-1),('INTEGER',str(-(2**63)),-(2**63)),
 ('REAL',' .125e+2 ',12.5),('REAL','',None),('REAL',2,2.0),('REAL',2.75,2.75),
 ('TEXT',b'caf\xc3\xa9','café'),('TEXT',12,'12'),('TEXT',1.5,'1.5'),('TEXT','NULL','NULL'),
 ('BLOB','café','café'.encode()),('BLOB',b'\x00\xff',b'\x00\xff'),
 ('INTEGER',None,None),('REAL',None,None),('TEXT',None,None),('BLOB',None,None),
])
def test_explicit_conversion(target,value,expected):
    db=Database(memory=True);db.execute('CREATE TABLE t(v)');db.execute('INSERT INTO t VALUES(?)',[value]);db.conn.commit()
    apply(db, {'t':{'types':{'v':target}}})
    actual=rows(db,'SELECT v FROM t')[0][0]
    assert actual==expected and type(actual) is type(expected)


@pytest.mark.parametrize('target,value',[
 ('INTEGER','3.2'),('INTEGER','1e99'),('INTEGER',str(2**63)),('INTEGER',b'12'),('INTEGER','0x10'),
 ('INTEGER','1_000'),('INTEGER','NaN'),('INTEGER',float('inf')),('INTEGER',1.25),
 ('REAL','NaN'),('REAL','Infinity'),('REAL','1e999'),('REAL',b'12'),('REAL','１２'),
 ('TEXT',b'\xff'),('BLOB',12),('BLOB',1.2),
])
def test_conversion_failure_rolls_back_prior_table(target,value):
    db=Database(memory=True);db.executescript('CREATE TABLE a(old TEXT); INSERT INTO a VALUES("ok"); CREATE TABLE z(v);')
    db.execute('INSERT INTO z VALUES(?)',[value]);db.conn.commit()
    error_unchanged(db, {'a':{'rename':{'old':'new'}},'z':{'types':{'v':target}}})


@pytest.mark.parametrize('literal', ['NULL','TRUE','CURRENT_TIMESTAMP',"'quoted'",'foo(bar)','a"b','a\x00b',12,2.5,True,False])
def test_default_values_are_literals(literal):
    db=Database(memory=True);db.executescript('CREATE TABLE t(id INTEGER PRIMARY KEY,v); INSERT INTO t VALUES(1,NULL);')
    apply(db, {'t':{'defaults':{'v':literal}}})
    assert rows(db,'SELECT v FROM t')==[(None,)]
    db.execute('INSERT INTO t(id) VALUES(2)');db.conn.commit()
    assert rows(db,'SELECT v FROM t WHERE id=2')==[(int(literal) if type(literal) is bool else literal,)]


def test_original_defaults_collations_and_constraint_policies():
    db=Database(memory=True)
    db.executescript('''CREATE TABLE t(id INTEGER PRIMARY KEY,
      label TEXT COLLATE NOCASE CONSTRAINT uq UNIQUE ON CONFLICT IGNORE,
      n INTEGER CONSTRAINT nn NOT NULL ON CONFLICT ABORT DEFAULT (+5),
      stamp TEXT DEFAULT CURRENT_TIMESTAMP, x BLOB DEFAULT X'ff',
      old TEXT CONSTRAINT olddefault DEFAULT ('hello' || ' world'),
      CONSTRAINT guard CHECK(n>0));
    INSERT INTO t(id,label,n) VALUES(1,'ABC',6);''')
    apply(db, {'t':{'defaults':{'old':None},'not_null':{'n':False},'column_order':['n']}})
    db.execute("INSERT INTO t(id,label) VALUES(2,'abc')");db.conn.commit()
    assert db['t'].count==1
    db.execute("INSERT INTO t(id,label) VALUES(3,'different')");db.conn.commit()
    assert rows(db,'SELECT n,x,old,stamp IS NOT NULL FROM t WHERE id=3')==[(5,b'\xff',None,1)]
    db.execute('UPDATE t SET n=NULL WHERE id=3');db.conn.commit()
    with pytest.raises(sqlite3.IntegrityError):db.execute('UPDATE t SET n=-1 WHERE id=1')
    db.conn.rollback()


@pytest.mark.parametrize('bad',[{},[],{'missing':{}},{'t':{'typo':{}}},{'T':{},'t':{}},
 {'t':{'rename':{'v':'id'}}},{'t':{'rename':{'v':12}}},
 {'t':{'types':{'v':'NUMERIC'}}},{'t':{'types':{'v':[]}}},{'t':{'types':{'id':'INTEGER'}}},
 {'t':{'not_null':{'id':True}}},{'t':{'not_null':{'v':1}}},{'t':{'strict':1}},
 {'t':{'defaults':{'v':{}}}},{'t':{'defaults':{'v':float('inf')}}},{'t':{'drop':['v','V']}},
 {'t':{'drop':['v'],'types':{'v':'INTEGER'}}},{'t':{'drop':['v'],'column_order':['v']}},
 {'t':{'column_order':['v','V']}},{'t':{'types':{'v':'TEXT','V':'INTEGER'}}},
 {'t':{'column_order':['missing']}},{'t':{'drop':'v'}},{'t':{'rename':[]}},
])
def test_plan_validation_is_atomic(bad):
    db=Database(memory=True);db.executescript('CREATE TABLE t(id INTEGER PRIMARY KEY,v TEXT);INSERT INTO t VALUES(1,"2");')
    error_unchanged(db,bad)


@pytest.mark.parametrize('create',[
 'CREATE TABLE t(k INTEGER PRIMARY KEY) WITHOUT ROWID',
 'CREATE TABLE t(x INTEGER, y INTEGER GENERATED ALWAYS AS(x+1))',
 'CREATE TABLE t(rowid TEXT,_rowid_ TEXT,oid TEXT)',
 'CREATE VIRTUAL TABLE t USING fts5(v)',
])
def test_unsupported_selected_schema_rejected(create):
    db=Database(memory=True);db.execute(create);error_unchanged(db,{'t':{}})


@pytest.mark.parametrize('dependency',[
 'CREATE VIEW v AS SELECT gone FROM z',
 'CREATE INDEX ix ON z(gone)',
 'CREATE INDEX ix ON z(length(gone)) WHERE gone IS NOT NULL',
 'CREATE TRIGGER tr AFTER INSERT ON z BEGIN SELECT NEW.gone; END',
])
def test_drop_dependency_rejected_without_losing_prior_changes(dependency):
    db=Database(memory=True);db.executescript('CREATE TABLE a(old TEXT);INSERT INTO a VALUES("ok");CREATE TABLE z(id INTEGER PRIMARY KEY,gone TEXT);INSERT INTO z VALUES(1,"x");'+dependency)
    error_unchanged(db,{'a':{'rename':{'old':'new'}},'z':{'drop':['gone']}})


@pytest.mark.parametrize('constraint', ['UNIQUE ON CONFLICT IGNORE','UNIQUE ON CONFLICT REPLACE','CHECK(v < 2)'])
def test_constraint_conversion_does_not_discard_or_replace(constraint):
    db=Database(memory=True)
    if constraint.startswith('CHECK'):
        db.executescript('CREATE TABLE a(old);CREATE TABLE z(v TEXT CHECK(typeof(v)="text"));INSERT INTO z VALUES("1");')
    else:
        db.executescript('CREATE TABLE a(old);CREATE TABLE z(v TEXT '+constraint+');INSERT INTO z VALUES("01"),("1");')
    error_unchanged(db,{'a':{'rename':{'old':'new'}},'z':{'types':{'v':'INTEGER'}}})


def test_unique_index_failure_after_copy_rolls_back_everything():
    db=Database(memory=True)
    db.executescript('CREATE TABLE a(id INTEGER PRIMARY KEY AUTOINCREMENT,v);INSERT INTO a VALUES(100,"x");DELETE FROM a;CREATE TABLE z(v TEXT);INSERT INTO z VALUES("01"),("1");CREATE UNIQUE INDEX uniq ON z(v);')
    error_unchanged(db,{'a':{'rename':{'v':'value'}},'z':{'types':{'v':'INTEGER'}}})


def test_not_null_default_does_not_fill_null():
    db=Database(memory=True);db.executescript('CREATE TABLE t(v TEXT);INSERT INTO t VALUES(NULL);')
    error_unchanged(db,{'t':{'not_null':{'v':True},'defaults':{'v':'fallback'}}})


def test_existing_invalid_foreign_key_rejected():
    db=Database(memory=True);db.executescript('CREATE TABLE p(id INTEGER PRIMARY KEY);CREATE TABLE t(pid REFERENCES p(id));INSERT INTO t VALUES(9);')
    error_unchanged(db,{'p':{}})


def test_conversion_invalidates_fk_rolls_back():
    db=Database(memory=True);db.executescript('CREATE TABLE p(k TEXT PRIMARY KEY);CREATE TABLE t(f TEXT REFERENCES p(k));INSERT INTO p VALUES("01");INSERT INTO t VALUES("01");')
    error_unchanged(db,{'t':{'types':{'f':'INTEGER'}}})


@pytest.mark.parametrize('dry_run',[False,True])
def test_caller_transaction_never_committed_or_rolled_back(dry_run):
    db=Database(memory=True);db.execute('CREATE TABLE t(v)');db.execute('BEGIN');db.execute('INSERT INTO t VALUES(1)')
    before=state(db)
    with pytest.raises(TransformError):apply(db,{'t':{'rename':{'v':'w'}}},dry_run=dry_run)
    assert db.conn.in_transaction and state(db)==before
    db.conn.rollback();assert db['t'].count==0


@pytest.mark.parametrize('fk,legacy,defer',[(0,0,0),(1,0,1),(0,1,1),(1,1,0)])
@pytest.mark.parametrize('fails',[False,True])
def test_connection_settings_restored(fk,legacy,defer,fails):
    db=Database(memory=True);db.executescript('CREATE TABLE t(v TEXT);INSERT INTO t VALUES("bad");')
    for key,val in [('foreign_keys',fk),('legacy_alter_table',legacy),('defer_foreign_keys',defer)]:db.execute(f'PRAGMA {key}={val}')
    plan={'t':{'types':{'v':'INTEGER'}}} if fails else {'t':{'rename':{'v':'w'}}}
    if fails:
        with pytest.raises(TransformError):apply(db,plan)
    else:apply(db,plan)
    assert db.execute('PRAGMA defer_foreign_keys').fetchone()[0]==defer
    assert db.execute('PRAGMA foreign_keys').fetchone()[0]==fk
    assert db.execute('PRAGMA legacy_alter_table').fetchone()[0]==legacy


def test_dry_run_readonly_file_and_wal_snapshot(tmp_path):
    path=tmp_path/'db.sqlite';writer=sqlite3.connect(path)
    writer.execute('PRAGMA journal_mode=WAL');writer.execute('CREATE TABLE t(v TEXT)');writer.execute("INSERT INTO t VALUES('12')");writer.commit()
    reader=Database(sqlite3.connect(f'file:{path}?mode=ro',uri=True))
    files={p:p.read_bytes() for p in (path, tmp_path/'db.sqlite-wal')}
    preview=apply(reader,{'t':{'types':{'v':'INTEGER'}}},dry_run=True)
    assert preview['tables']==[{'name':'t','rows':1,'columns':['v']}]
    assert all(p.read_bytes()==b for p,b in files.items())
    assert rows(reader,'SELECT v,typeof(v) FROM t')==[('12','text')]
    reader.conn.close();writer.close()
    db=Database(path);assert apply(db,{'t':{'types':{'v':'INTEGER'}}})==preview


def test_concurrent_writer_busy_leaves_schema_unchanged(tmp_path):
    path=tmp_path/'db.sqlite';db=Database(path);db.executescript('CREATE TABLE t(v);INSERT INTO t VALUES(1);')
    blocker=sqlite3.connect(path);blocker.execute('BEGIN IMMEDIATE')
    db.execute('PRAGMA busy_timeout=0')
    before=state(db)
    with pytest.raises((TransformError,sqlite3.OperationalError)):apply(db,{'t':{'rename':{'v':'w'}}})
    assert state(db)==before and not db.conn.in_transaction
    blocker.rollback();blocker.close()


def test_deterministic_late_authorization_failure_rolls_back(tmp_path):
    path=tmp_path/'db.sqlite';db=Database(path);db.executescript('CREATE TABLE a(old);CREATE TABLE z(old);INSERT INTO a VALUES(1);INSERT INTO z VALUES(2);')
    before=state(db)
    def authorize(action, arg1,arg2, database, source):
        if (action==sqlite3.SQLITE_DROP_TABLE and arg1=='z') or (action==sqlite3.SQLITE_ALTER_TABLE and arg2=='z'):return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK
    db.conn.set_authorizer(authorize)
    with pytest.raises(TransformError):apply(db,{'a':{'rename':{'old':'new'}},'z':{'rename':{'old':'new'}}})
    db.conn.set_authorizer(None)
    assert state(db)==before and not db.conn.in_transaction
    db.conn.close();again=Database(path);assert rows(again,'SELECT old FROM a')==[(1,)]


def test_cli_json_preview_execute_and_error(tmp_path):
    path=tmp_path/'db.sqlite';db=Database(path);db.executescript('CREATE TABLE t(old TEXT);INSERT INTO t VALUES("12");');db.conn.close()
    plan=tmp_path/'plan.json';plan.write_text(json.dumps({'t':{'rename':{'old':'new'},'types':{'old':'INTEGER'}}}))
    before=hashlib.sha256(path.read_bytes()).hexdigest();runner=CliRunner()
    preview=runner.invoke(cli,['transform-schema',str(path),str(plan),'--dry-run'])
    assert preview.exit_code==0,preview.output
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before
    result=runner.invoke(cli,['transform-schema',str(path),str(plan)])
    assert result.exit_code==0,result.output
    assert json.loads(result.output)==json.loads(preview.output)
    assert rows(Database(path),'SELECT new FROM t')==[(12,)]
    failed=runner.invoke(cli,['transform-schema',str(path),str(plan)])
    assert failed.exit_code!=0 and 'Error:' in failed.output and 'Traceback' not in failed.output
    plan.write_text('{invalid')
    malformed=runner.invoke(cli,['transform-schema',str(path),str(plan)])
    assert malformed.exit_code!=0 and 'Traceback' not in malformed.output


@pytest.mark.parametrize("succeeds", [False, True])
@pytest.mark.parametrize("isolation", [None, "DEFERRED"])
def test_supported_connection_modes_finish_transaction(succeeds,isolation):
    db=Database(sqlite3.connect(":memory:", isolation_level=isolation))
    db.executescript("CREATE TABLE t(v TEXT);INSERT INTO t VALUES('bad');")
    before=state(db)
    if succeeds:
        apply(db, {"t":{"rename":{"v":"w"}}})
        assert rows(db,"SELECT w FROM t")==[("bad",)]
    else:
        with pytest.raises(TransformError):apply(db,{"t":{"types":{"v":"INTEGER"}}})
        assert state(db)==before
    assert not db.conn.in_transaction


def test_process_death_between_table_changes_recovers_all_original_state(tmp_path):
    path=tmp_path/"crash.sqlite"
    db=Database(path)
    db.executescript("CREATE TABLE a(old TEXT);INSERT INTO a VALUES('a');CREATE TABLE z(old TEXT);INSERT INTO z VALUES('z');CREATE VIEW v AS SELECT old FROM a;")
    before=state(db);db.conn.close()
    # A SQLite authorizer is an existing public connection hook. Exit immediately
    # before changing the second table, whether using native ALTER or a rebuild.
    code = r'''
import os, sqlite3, sys
from sqlite_utils import Database
db=Database(sys.argv[1])
assert callable(getattr(db, "transform_schema", None)), "missing feature"
def stop(action, a1, a2, database, source):
    if (action==sqlite3.SQLITE_DROP_TABLE and a1=="z") or (action==sqlite3.SQLITE_ALTER_TABLE and a2=="z"):os._exit(73)
    return sqlite3.SQLITE_OK
db.conn.set_authorizer(stop)
db.transform_schema({"a":{"rename":{"old":"new"}},"z":{"rename":{"old":"new"}}})
'''
    result=subprocess.run([sys.executable,"-c",code,str(path)],capture_output=True,text=True,timeout=20)
    assert result.returncode==73, result.stderr
    reopened=Database(path)
    assert state(reopened)==before
    assert rows(reopened,"SELECT * FROM v")==[("a",)]
    assert rows(reopened,"PRAGMA integrity_check")==[("ok",)]


def test_constraint_keyword_column_does_not_hide_table_constraint():
    db=Database(memory=True)
    db.executescript('CREATE TABLE t("CONSTRAINT" TEXT, v INTEGER, CONSTRAINT positive CHECK(v>0));INSERT INTO t VALUES("literal",1);')
    apply(db,{"t":{"column_order":["v"]}})
    assert rows(db,'SELECT "CONSTRAINT",v FROM t')==[("literal",1)]
    with pytest.raises(sqlite3.IntegrityError):db.execute("INSERT INTO t(v) VALUES(-1)")
    db.conn.rollback()



def test_mapping_api_and_dry_run_error_settings():
    from collections import UserDict
    db=Database(memory=True)
    db.executescript("CREATE TABLE t(v TEXT);INSERT INTO t VALUES('2');")
    plan=UserDict({"t":UserDict({"types":UserDict({"v":int})})})
    apply(db,plan)
    assert rows(db,"SELECT v FROM t")==[(2,)]
    db.execute("PRAGMA defer_foreign_keys=ON")
    with pytest.raises(TransformError):apply(db,{"t":{"types":{"v":"BLOB"}}},dry_run=True)
    assert db.execute("PRAGMA defer_foreign_keys").fetchone()[0]==1
    assert rows(db,"SELECT v FROM t")==[(2,)]
