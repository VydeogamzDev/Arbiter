import argparse
import datetime


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("name")
    p.add_argument("--verbose", action="store_true")
    a = p.parse_args(argv)
    print(f"Hello, {a.name}!")
    if a.verbose:
        print(a.name.upper(), datetime.datetime.now().isoformat())


if __name__ == "__main__":
    main()
