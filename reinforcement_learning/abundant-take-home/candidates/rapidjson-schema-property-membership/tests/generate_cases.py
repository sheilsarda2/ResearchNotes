"""Generate fixed independent Draft-4 instance-validation fixtures."""
import json
from pathlib import Path

cases=[]
def add(name,schema,instance,expected):
    cases.append(dict(name=name,schema=schema,instance=instance,expected=expected))
add('required_undeclared_forbidden',{'properties':{'B':{}},'required':['B','C'],'additionalProperties':False},{'B':1,'C':1},False)
add('missing_required',{'properties':{'B':{}},'required':['B','C'],'additionalProperties':False},{'B':1},False)
add('declared_required',{'properties':{'B':{},'C':{}},'required':['B','C'],'additionalProperties':False},{'B':1,'C':1},True)
add('required_by_pattern',{'patternProperties':{'^C$':{'type':'number'}},'required':['C'],'additionalProperties':False},{'C':1},True)
add('required_pattern_type',{'patternProperties':{'^C$':{'type':'number'}},'required':['C'],'additionalProperties':False},{'C':'x'},False)
for value in ['wrong',3]:
    add('required_extra_schema_'+str(value),{'required':['C'],'additionalProperties':{'type':'number'}},{'C':value},isinstance(value,int))
add('only_required_present_forbidden',{'required':['C'],'additionalProperties':False},{'C':1},False)
add('required_default_additional',{'required':['C']},{'C':1},True)
add('required_explicit_true',{'required':['C'],'additionalProperties':True},{'C':[1,2]},True)
add('ordinary_extra_forbidden',{'properties':{'B':{}},'additionalProperties':False},{'C':1},False)
add('empty_object_allowed',{'properties':{'B':{}},'additionalProperties':False},{},True)
for source,target in [('A',None),('A','B')]:
    s={'properties':{'B':{}},'dependencies':{'A':['B']},'additionalProperties':False}
    d={'A':1};
    if target:d[target]=2
    add('dependency_source_forbidden_'+str(target),s,d,False)
add('dependency_target_forbidden',{'properties':{'A':{}},'dependencies':{'A':['B']},'additionalProperties':False},{'A':1,'B':2},False)
add('dependencies_declared_valid',{'properties':{'A':{},'B':{}},'dependencies':{'A':['B']},'additionalProperties':False},{'A':1,'B':2},True)
add('dependencies_missing_target',{'properties':{'A':{},'B':{}},'dependencies':{'A':['B']},'additionalProperties':False},{'A':1},False)
add('dependencies_inactive',{'dependencies':{'A':['B']},'additionalProperties':False},{},True)
add('dependencies_additional_allowed',{'dependencies':{'A':['B']}},{'A':1,'B':2},True)
add('dependency_extra_schema_type',{'properties':{'A':{}},'dependencies':{'A':['B']},'additionalProperties':{'type':'integer'}},{'A':1,'B':'x'},False)
add('dependency_extra_schema_valid',{'properties':{'A':{}},'dependencies':{'A':['B']},'additionalProperties':{'type':'integer'}},{'A':1,'B':2},True)
add('dependency_pattern_presence',{'patternProperties':{'^[AB]$':{'type':'number'}},'dependencies':{'A':['B']},'additionalProperties':False},{'A':1,'B':2},True)
add('dependency_pattern_missing',{'patternProperties':{'^[AB]$':{'type':'number'}},'dependencies':{'A':['B']},'additionalProperties':False},{'A':1},False)
add('schema_dependency_source_forbidden',{'properties':{'B':{}},'dependencies':{'A':{'required':['B']}},'additionalProperties':False},{'A':1,'B':2},False)
add('schema_dependency_missing',{'properties':{'A':{},'B':{}},'dependencies':{'A':{'required':['B']}},'additionalProperties':False},{'A':1},False)
add('schema_dependency_valid',{'properties':{'A':{},'B':{}},'dependencies':{'A':{'required':['B']}},'additionalProperties':False},{'A':1,'B':2},True)
add('pattern_wins_over_extra_schema',{'required':['C'],'patternProperties':{'^C$':{'type':'number'}},'additionalProperties':{'type':'string'}},{'C':2},True)
add('pattern_not_waived_by_extra_schema',{'required':['C'],'patternProperties':{'^C$':{'type':'number'}},'additionalProperties':{'type':'string'}},{'C':'x'},False)
add('declared_empty_schema_is_declared',{'properties':{'C':{}},'required':['C'],'additionalProperties':{'type':'number'}},{'C':'free'},True)
add('ref_property_is_declared',{'definitions':{'free':{}},'properties':{'C':{'$ref':'#/definitions/free'}},'required':['C'],'additionalProperties':False},{'C':'free'},True)
add('nested_required_extra',{'properties':{'outer':{'required':['C'],'additionalProperties':False}}},{'outer':{'C':1}},False)
add('nested_parent_names_do_not_declare',{'properties':{'C':{},'outer':{'required':['C'],'additionalProperties':False}}},{'C':1,'outer':{'C':2}},False)
add('array_items_required_extra',{'type':'array','items':{'required':['C'],'additionalProperties':{'type':'number'}}},[{'C':1},{'C':'x'}],False)
add('array_items_valid',{'type':'array','items':{'required':['C'],'additionalProperties':{'type':'number'}}},[{'C':1},{'C':2}],True)
add('allof_required_extra',{'allOf':[{'required':['C'],'additionalProperties':False}]},{'C':1},False)
add('nullbyte_name_forbidden',{'required':['C\x00D'],'additionalProperties':False},{'C\x00D':1},False)
add('unicode_name_schema',{'required':['日本語'],'additionalProperties':{'type':'number'}},{'日本語':'x'},False)
add('unicode_declared',{'properties':{'日本語':{}},'required':['日本語'],'additionalProperties':False},{'日本語':'x'},True)

# Separate permission from dependency activation even when the trigger is undeclared.
add('property_dependency_undeclared_source_missing',{'dependencies':{'A':['B']}},{'A':1},False)
add('schema_dependency_undeclared_source_missing',{'dependencies':{'A':{'required':['B']}}},{'A':1},False)
add('schema_dependency_undeclared_source_valid',{'dependencies':{'A':{'required':['B']}}},{'A':1,'B':2},True)
add('declared_nullbyte_name',{'properties':{'C\x00D':{}},'required':['C\x00D'],'additionalProperties':False},{'C\x00D':1},True)
add('nullbyte_name_does_not_match_prefix',{'properties':{'C':{}},'required':['C\x00D'],'additionalProperties':False},{'C\x00D':1},False)
add('ref_property_still_validates',{'definitions':{'number':{'type':'number'}},'properties':{'C':{'$ref':'#/definitions/number'}},'required':['C'],'additionalProperties':False},{'C':'wrong'},False)
for value in [1,2]:
    add('all_matching_patterns_'+str(value),{'required':['C'],'patternProperties':{'^C':{'type':'integer'},'C$':{'minimum':2}},'additionalProperties':False},{'C':value},value==2)

# Change documents between Reset calls, so stale presence bits or errors cannot pass.
add('reset_required_presence',{'required':['C'],'additionalProperties':{'type':'number'}},{'C':1},True)
cases[-1]['after_reset']=[{'instance':{},'expected':False},{'instance':{'C':2},'expected':True}]
add('reset_property_dependency_presence',{'dependencies':{'A':['B']},'additionalProperties':{'type':'integer'}},{'A':1,'B':2},True)
cases[-1]['after_reset']=[{'instance':{'A':1},'expected':False},{'instance':{},'expected':True},{'instance':{'A':3,'B':4},'expected':True}]
add('reset_schema_dependency_presence',{'dependencies':{'A':{'required':['B']}}},{'A':1,'B':2},True)
cases[-1]['after_reset']=[{'instance':{'A':1},'expected':False},{'instance':{},'expected':True},{'instance':{'A':3,'B':4},'expected':True}]

out=Path(__file__).with_name('cases.json')
out.write_text(json.dumps(cases,ensure_ascii=True,indent=2)+'\n')
print(len(cases),'cases')
