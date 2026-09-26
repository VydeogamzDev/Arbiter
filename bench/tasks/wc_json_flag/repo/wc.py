import argparse
import sys


def counts(text):
    return len(text.splitlines()), len(text.split()), len(text)


def main(argv=None):
    p = argparse.ArgumentParser(description="count lines, words and chars")
    p.add_argument("file")
    a = p.parse_args(argv)
    text = open(a.file, encoding="utf-8").read()
    lines, words, chars = counts(text)
    print(f"{lines} {words} {chars} {a.file}")


if __name__ == "__main__":
    sys.exit(main())
