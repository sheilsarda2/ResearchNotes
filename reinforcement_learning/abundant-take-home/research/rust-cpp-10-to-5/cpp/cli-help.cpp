#include <CLI/CLI.hpp>

int main(int argc, char** argv) {
    CLI::App app{"CLI11 help bug MRE"};

    auto* subc1 = app.add_subcommand("subc1", "First subcommand");

    int id{};
    subc1->add_option("ID", id, "Object ID")->required();

    auto* subc2 = subc1->add_subcommand("subc2", "Nested subcommand");

    bool flag{false};
    subc2->add_flag("--flag", flag, "Example flag");

    CLI11_PARSE(app, argc, argv);

    return 0;
}
