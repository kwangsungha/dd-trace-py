from ddtrace.internal import core


def run_session():
    with core.context_with_data("manual_testing_api.run_session", session_name="my_session") as ctx:
        print(ctx.get_item("session_tree"))
        ctx.set_item("session_tree", {"session_name": "my_session", "modules": {}})
        print(ctx.get_item("session_tree"))
        for module in ["module_1", "module_2", "module_3"]:
            run_module(module)

        print(ctx.get_item("module_name"))
        print(ctx.get_item("suite_name"))
        print(ctx.get_item("test_name"))
        from pprint import pprint

        pprint(ctx.get_item("session_tree"))


def run_module(module_name):
    with core.context_with_data("manual_testing_api.run_module", module_name=module_name) as ctx:
        ctx.get_item("session_tree")["modules"][module_name] = {}
        for suite in ["suite_1", "suite_2"]:
            run_suite(suite)


def run_suite(suite_name):
    with core.context_with_data("manual_testing_api.suite_data", suite_name=suite_name) as ctx:
        session_tree = ctx.get_item("session_tree")
        module_name = ctx.get_item("module_name")
        session_tree["modules"][module_name][suite_name] = {}
        for test in ["test_1", "test_2", "test_3", "test_4"]:
            run_test(test)


def run_test(test_name):
    with core.context_with_data("manual_testing_api.test_data", test_name=test_name) as ctx:
        session_tree = ctx.get_item("session_tree")
        module_name = ctx.get_item("module_name")
        suite_name = ctx.get_item("suite_name")
        session_tree["modules"][module_name][suite_name][test_name] = {"status": "pass"}


def main():
    with core.context_with_data("manual_testing_api.main") as ctx:
        ctx.set_item("session_tree", "my_string")
        run_session()
        print(ctx.get_item("session_tree"))


if __name__ == "__main__":
    main()
