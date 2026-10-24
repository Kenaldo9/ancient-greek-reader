"""
Whether the network this runs on reaches only the internet. The public
site's deploy runs it in a throwaway container on the tunnel's network
before cloudflared starts (deploy/public/common.sh, check_tunnel_walls):

    python probe_walls.py --open LABEL=HOST:PORT ... LABEL=HOST:PORT ...

No address given plainly may answer: one that accepts or refuses a
connection is reachable, one that stays silent is walled off. Each --open
address must accept, which shows the probe itself can connect. It prints
labels, not addresses: a deploy's log is public.
"""

import argparse
import socket
import sys
from concurrent.futures import ThreadPoolExecutor

TIMEOUT = 3.0


def parse_target(text: str) -> tuple[str, str, int]:
    """'the home router=192.168.1.1:80' → ('the home router', '192.168.1.1', 80)"""
    label, sep, address = text.rpartition("=")
    host, colon, port = address.rpartition(":")
    if not (sep and label and colon and host and port.isdigit()):
        raise argparse.ArgumentTypeError(f"not LABEL=HOST:PORT: {text!r}")
    return label, host, int(port)


def answers(host: str, port: int, timeout: float = TIMEOUT) -> bool:
    """Whether anything at host:port answers, by accepting or by refusing."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except ConnectionRefusedError:
        return True
    except OSError:  # silence (a timeout), or no route
        return False


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--open",
        action="append",
        default=[],
        type=parse_target,
        metavar="LABEL=HOST:PORT",
        help="an address on the internet that must answer",
    )
    parser.add_argument(
        "walled",
        nargs="+",
        type=parse_target,
        metavar="LABEL=HOST:PORT",
        help="an address that must not answer",
    )
    args = parser.parse_args(argv)
    targets = args.walled + args.open
    with ThreadPoolExecutor(max_workers=len(targets)) as pool:
        reached = list(pool.map(lambda t: answers(t[1], t[2]), targets))
    walled, opened = reached[: len(args.walled)], reached[len(args.walled) :]

    problems = [f"it reaches {t[0]}" for t, r in zip(args.walled, walled) if r]
    problems += [
        f"it does not reach {t[0]}" for t, r in zip(args.open, opened) if not r
    ]
    if problems:
        print("The tunnel's network: " + "; ".join(problems) + ".")
        return 1
    print(
        f"The tunnel's network reaches none of the {len(args.walled)} addresses "
        "on the Pi, the home network and the tailnet it tried"
        + (f", and reaches {', '.join(t[0] for t in args.open)}" if args.open else "")
        + "."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
