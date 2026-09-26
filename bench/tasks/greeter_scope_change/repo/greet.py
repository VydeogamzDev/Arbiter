import argparse


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("name")
    a = p.parse_args(argv)
    print(f"Hello, {a.name}!")


if __name__ == "__main__":
    main()
