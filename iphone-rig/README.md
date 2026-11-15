# iPhone Rig

A solo Bitcoin "lottery" miner that runs on a stock iPhone. No jailbreak, no
computer, no sideloading: one Python file inside a free App Store app.

## What to expect

This is a lottery ticket, not an income.

- It pays **nothing** unless it finds a whole Bitcoin block. If it ever does,
  the full block reward goes to your address.
- The chance is tiny, and the miner prints the real figure for your phone
  and the current Bitcoin difficulty (`odds 1 in ... per year`). Expect
  something like 1 in several billion per year.
- No software on an unmodified iPhone does better than "tiny". See
  [SURVEY.md](SURVEY.md) for what else exists and why.

## Start mining (about 5 minutes)

1. Install **a-Shell** from the App Store (free).
2. Have a Bitcoin address ready, from a wallet app where you hold the keys.
   It starts with `bc1`, `1` or `3`.
3. Open a-Shell and paste these two lines:

   ```
   curl -L -o miner.py https://raw.githubusercontent.com/shashankbhasin21-spec/you-and-insta/claude/kind-ritchie-eep6of/iphone-rig/miner.py
   python3 miner.py
   ```

4. Paste your address when it asks. It checks the address for typos and
   remembers it, so from then on you only type `python3 miner.py`.

It then connects by itself and you should see lines like these (your numbers
will differ):

```
engine   6 cores, 4.10 MH/s
12:01:02  connecting to public-pool.io:3333
12:01:03  connected
12:01:03  block 921034  net diff 147T
12:01:03  prize 3.142 BTC, 100% to you
12:01:03  odds 1 in 4.88 billion per year
12:01:21  accepted #1  diff 0.0312  212ms
12:01:33  4.02 MH/s  A1 R0  best 0.0312
```

`accepted` means the pool checked a piece of your work and it was correct.
`A`/`R` count accepted and rejected shares. `best` is your luckiest hash so
far; a block needs a `best` as big as `net diff`.

To stop, press Ctrl-C (a-Shell has a control key in the bar above its
keyboard).

## Keep it running 24/7

iOS pauses any app that is not on screen, so:

- **Settings > Display & Brightness > Auto-Lock > Never**, and leave a-Shell
  open on screen. Turn the brightness right down.
- Keep the phone on a charger.
- **Settings > Battery > Charging**: set the charge limit to 80% if your iOS
  version offers it. A phone that sits at 100% and warm all day wears its
  battery faster.
- Take the case off. If the phone feels hot, use fewer cores:
  `python3 miner.py --cores 2` (remembered for next time).

If you switch apps or the screen locks, mining pauses. When you come back, the
miner notices, reconnects and carries on by itself.

## What it does on its own

| Hive OS idea | In this rig |
|---|---|
| Flight sheets | A named coin and pool list. `btc-solo` is built in; add your own in `flightsheets.json` |
| Backup pools | Five solo pool addresses are tried in order; a dead one is skipped |
| Watchdog | Reconnects after sleep, network loss or a silent pool; drops to one core if all-core mode fails |
| Tuning | Tests every SHA-256 the phone's Python has and uses the fastest; uses every core when that is faster |
| Dashboard | `python3 miner.py --web`, then open `http://127.0.0.1:8080` |
| Stats | Hashrate, shares, best share ever and total hashes are kept between runs in `rig_stats.json` |

Two safety checks Hive OS does not have:

- **Self-test.** Before mining, every hashing method must reproduce the hash
  of real Bitcoin block 125552. One that cannot is not used.
- **Payout check.** Every job is read to confirm the block reward is paid to
  your address. A pool that pays someone else gets no hashes from you.

## Options

| Command | What it does |
|---|---|
| `python3 miner.py` | Start with the saved settings |
| `python3 miner.py --cores 2` | Use 2 cores (cooler). `--cores 1` is the coolest |
| `python3 miner.py --intensity 60` | Hash 60% of the time and rest 40% |
| `python3 miner.py --web` | Also serve the dashboard on port 8080 |
| `python3 miner.py --web --web-host 0.0.0.0` | Let other devices on your Wi-Fi open the dashboard at `http://<phone's IP>:8080` |
| `python3 miner.py --worker bedroom` | Name shown on the pool's page |
| `python3 miner.py --benchmark` | Measure the phone's speed and exit |
| `python3 miner.py --sheets` | List flight sheets and their pools |
| `python3 miner.py --forget` | Forget the saved address and settings |

Settings you pass are remembered in `rig.json`.

On a-Shell the dashboard only updates while a-Shell is on screen, so it is
most useful from a second device on the same Wi-Fi, or in the mode below.

## Your own pools

Create `flightsheets.json` next to `miner.py`:

```json
{
  "my-pool": {
    "coin": "BTC",
    "payout_check": "enforce",
    "pools": ["my.pool.example:3333", "backup.pool.example:3333"]
  }
}
```

Then run `python3 miner.py --sheet my-pool`. Any Stratum v1 pool for a
SHA-256 coin works. `payout_check` can be `enforce` (solo pools: refuse work
that does not pay you), `warn` (shared pools, which pay themselves and credit
you later) or `off`.

## Screen-off mode with iSH (slower, not tested by me)

a-Shell cannot run with the screen locked. The **iSH** app can, by a method
its own wiki documents, but it emulates another processor, so expect it to be
many times slower.

```
apk add python3 curl
curl -L -o miner.py https://raw.githubusercontent.com/shashankbhasin21-spec/you-and-insta/claude/kind-ritchie-eep6of/iphone-rig/miner.py
cat /dev/location > /dev/null &
python3 miner.py --intensity 50 --web
```

Allow location access "Always" when asked; that is what keeps iSH alive in
the background. With `--web` you can watch the dashboard in Safari at
`http://127.0.0.1:8080`. Keep `--intensity` at 50 or lower here, because iOS
can close background apps that use a full core without pause.

## What has and has not been checked

Checked, with `python3 -m unittest discover tests` (24 tests):

- Hashing against real Bitcoin data: the genesis block rebuilt from a
  Stratum-style job, block 125552, block 170's merkle root, and the address
  examples from the Bitcoin standards.
- The whole miner against a test pool that re-checks every share on its own,
  following the header layout used by NerdMiner (a widely used hobby miner).
  All shares were accepted, on one core and in all-core mode.
- Failover to a backup pool, refusing a pool that pays someone else,
  reconnecting after a dropped connection or a simulated sleep, the
  block-found path, and the dashboard at phone width in light and dark.

Not checked:

- **A real iPhone.** It was built and tested on Linux with Python 3.13, the
  version a-Shell's README says it uses. All-core mode in particular is untried on iOS. If it
  cannot start there, the miner says so and uses one core.
- **The real pools.** The build machine could not reach them, so the first
  connection to public-pool.io will be on your phone. The pool list comes
  from NerdMiner's README.

If the first run prints an error instead of `connected`, send me the lines it
printed.
