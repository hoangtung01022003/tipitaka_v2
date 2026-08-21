"""Tách thử bản Indacanda trọn reader segment cho Cullavagga I; không ghi DB."""

from indacanda_full_extract import run_cli


if __name__ == "__main__":
    run_cli("cv1")
