from greet import main


def test_hello(capsys):
    main(["Ada"])
    assert capsys.readouterr().out == "Hello, Ada!\n"
