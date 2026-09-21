"""Compare three AvalAI models with the same Persian input."""

from app.check_avalai import main

if __name__ == "__main__":
    raise SystemExit(main(input_language="fa", compare=True))
