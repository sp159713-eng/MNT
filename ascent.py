import sys


def run_walkforward():
    import walkforward as walkforward_module

    sys.argv = ["walkforward"] + sys.argv[2:]
    try:
        walkforward_module.main()
    except SystemExit:
        raise
    except BaseException as error:
        print(f"{type(error).__name__}: {error}")
        sys.exit(1)
    sys.exit(0)


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--walkforward":
        run_walkforward()
    if "--classic" in sys.argv[1:]:
        import gui

        gui.App().mainloop()
        return
    from qtgui.app import main as fluent_main

    sys.exit(fluent_main())


if __name__ == "__main__":
    main()
