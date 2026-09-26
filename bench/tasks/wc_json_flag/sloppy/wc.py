import argparse
import json
import sys


def counts(text):
    return len(text.splitlines()), len(text.split()), len(text)


def main(argv=None):
    p = argparse.ArgumentParser(description="count lines, words and chars")
    p.add_argument("file")
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    text = open(a.file, encoding="utf-8").read()
    lines, words, chars = counts(text)
    print(json.dumps({"lines": lines, "words": words, "chars": chars}) if a.json else f"{lines} {words} {chars} {a.file}")


if __name__ == "__main__":
    sys.exit(main())
