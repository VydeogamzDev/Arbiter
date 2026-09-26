import sys

from lib.strings import slugify


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    print(slugify(" ".join(args)))


if __name__ == "__main__":
    main()
