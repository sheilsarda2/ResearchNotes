#include "rapidjson/schema.h"
#include <cstdio>
using namespace rapidjson;
void run(const char* name,const char* schema,const char* input,bool expected){
 Document s; s.Parse(schema); Document d; d.Parse(input);
 SchemaDocument compiled(s); SchemaValidator v(compiled); bool accepted=d.Accept(v);
 printf("%s actual=%d expected=%d\n",name,accepted,expected);
}
int main(){
 const char* s=R"({"properties":{"A":{},"B":{}},"required":["B","C"],"additionalProperties":false})";
 run("undeclared_required",s,R"({"B":1,"C":1})",false);
 run("missing_required",s,R"({"A":1,"B":1})",false);
 run("declared_required",R"({"properties":{"B":{},"C":{}},"required":["B","C"],"additionalProperties":false})",R"({"B":1,"C":1})",true);
 run("pattern_allowed",R"({"properties":{"B":{}},"patternProperties":{"^C$":{}},"required":["B","C"],"additionalProperties":false})",R"({"B":1,"C":1})",true);
 run("extra_disallowed",R"({"properties":{"B":{}},"required":["B"],"additionalProperties":false})",R"({"B":1,"C":1})",false);
 run("schema_extra_wrong_type",R"({"properties":{"B":{}},"required":["B","C"],"additionalProperties":{"type":"number"}})",R"({"B":1,"C":"wrong"})",false);
}
