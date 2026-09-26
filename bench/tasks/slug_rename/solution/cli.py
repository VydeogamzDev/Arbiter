import sys

from lib.strings import make_slug


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    print(make_slug(" ".join(args)))


if __name__ == "__main__":
    main()
