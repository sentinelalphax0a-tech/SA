"""
Quick Google News RSS test.

Searches for a popular topic and an obscure one.
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.scanner.news_checker import NewsChecker


def main() -> None:
    checker = NewsChecker()

    queries = [
        ("Trump president", 24),
        ("minister resign Andorra", 24),
    ]

    for keywords, hours in queries:
        print(f'Searching: "{keywords}" (last {hours}h)...')
        has_news, summary = checker.check_news(keywords, hours=hours)
        if has_news:
            print(f"  Found:   YES")
            print(f"  Headline: {summary}")
        else:
            print(f"  Found:   NO")
        print()

    print("\u2705 News checker test complete")


if __name__ == "__main__":
    main()
