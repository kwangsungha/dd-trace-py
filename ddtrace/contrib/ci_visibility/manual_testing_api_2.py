from ddtrace.internal import core


def run_session():
    with core.context_with_data("manual_testing_api.run_session", session_name="my_session") as ctx:
        print(ctx.get_item("session_tree"))
        session_tree = {"session_name": "my_session", "modules": {}}
        ctx.set_item("session_tree", session_tree)
        print(ctx.get_item("session_tree"))
        for module in ["module_1", "module_2", "module_3"]:
            module_data = {}
            session_tree["modules"][module] = module_data
            with core.context_with_data("manual_testing_api.run_session.module", module_data=module_data):
                run_module(module)

        print(ctx.get_item("module_name"))
        print(ctx.get_item("suite_name"))
        print(ctx.get_item("test_name"))
        from pprint import pprint

        pprint(ctx.get_item("session_tree"))


def run_module(module_name):
    with core.context_with_data("manual_testing_api.run_module") as ctx:
        module_data = ctx.get_item("module_data")

        for suite in ["suite_1", "suite_2"]:
            suite_data = {}
            module_data[suite] = suite_data
            with core.context_with_data("manual_testing_api.run_module.suite", suite_data=suite_data):
                run_suite(suite)


def run_suite(suite_name):
    with core.context_with_data("manual_testing_api.suite_data") as ctx:
        suite_data = ctx.get_item("suite_data")
        for test in ["test_1", "test_2", "test_3", "test_4"]:
            test_data = {}
            suite_data[test] = test_data
            with core.context_with_data("manual_testing_api.run_suite.test", test_data=test_data):
                run_test(test)


def run_test(test_name):
    with core.context_with_data("manual_testing_api.test_data") as ctx:
        test_data = ctx.get_item("test_data")
        test_data["status"] = "pass"


def main():
    with core.context_with_data("manual_testing_api.main") as ctx:
        ctx.set_item("session_tree", "my_string")
        run_session()
        print(ctx.get_item("session_tree"))


if __name__ == "__main__":
    main()
