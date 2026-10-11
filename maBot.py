"""Entry point kept for the systemd unit: ExecStart=... python maBot.py"""
from wgcop.app import main


if __name__ == "__main__":
    main()
