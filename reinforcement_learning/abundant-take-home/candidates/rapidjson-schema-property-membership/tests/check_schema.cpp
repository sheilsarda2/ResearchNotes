#include <rapidjson/schema.h>
#include <rapidjson/reader.h>
#include <rapidjson/writer.h>
#include <rapidjson/stringbuffer.h>
#include <fstream>
#include <sstream>
#include <cstdio>
#include <stdexcept>

using namespace rapidjson;

std::string encode(const Value& value) {
    StringBuffer buffer;
    Writer<StringBuffer> writer(buffer);
    if (!value.Accept(writer))
        throw std::runtime_error("fixture encoding failed");
    return std::string(buffer.GetString(), buffer.GetSize());
}

int main(int argc, char** argv) {
    if (argc != 2) return 2;
    std::ifstream file(argv[1]);
    if (!file) return 2;
    std::stringstream contents;
    contents << file.rdbuf();
    Document cases;
    cases.Parse(contents.str().c_str());
    if (cases.HasParseError() || !cases.IsArray()) return 2;
    unsigned checks = 0, failed = 0;
    for (auto& c : cases.GetArray()) {
        const std::string schema_text = encode(c["schema"]);
        Document schema_doc;
        schema_doc.Parse(schema_text.c_str());
        if (schema_doc.HasParseError()) return 2;
        SchemaDocument schema(schema_doc);
        const bool has_sequence = c.HasMember("after_reset");
        const unsigned steps = has_sequence ? c["after_reset"].Size() + 1 : 2;
        // Run DOM, SAX, and continued error collection with the same schema.
        for (int mode = 0; mode < 3; mode++) {
            SchemaValidator validator(schema);
            if (mode == 2) validator.SetValidateFlags(kValidateContinueOnErrorFlag);
            for (unsigned step = 0; step < steps; step++) {
                if (step) validator.Reset();
                const Value& fixture = step && has_sequence ? c["after_reset"][step - 1] : c;
                const bool expected = fixture["expected"].GetBool();
                const std::string instance_text = encode(fixture["instance"]);
                bool traversed;
                if (mode == 1) {
                    Reader reader;
                    StringStream stream(instance_text.c_str());
                    traversed = !reader.Parse(stream, validator).IsError();
                } else {
                    Document instance;
                    instance.Parse(instance_text.c_str());
                    if (instance.HasParseError()) return 2;
                    traversed = instance.Accept(validator);
                }
                const bool valid = validator.IsValid();
                checks++;
                if (valid != expected || (expected && !traversed)) {
                    failed++;
                    std::printf("FAIL %s mode=%d step=%u expected=%d observed=%d traversed=%d\n",
                                c["name"].GetString(), mode, step, expected, valid, traversed);
                }
            }
        }
    }
    std::printf("{\"cases\":%u,\"checks\":%u,\"failed\":%u}\n", cases.Size(), checks, failed);
    return failed ? 1 : 0;
}
