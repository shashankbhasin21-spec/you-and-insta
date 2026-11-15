# Survey: crypto mining on an iPhone (GitHub, 15 November 2026)

About 25 GitHub repository searches, and a closer read of the seven projects
marked "read". Star counts and dates are from the day of the survey. Nobody
can read every repository on GitHub; this covers the projects those searches
surfaced.

## Findings

1. **No repository mines in the background on an unmodified iPhone.** Every
   native miner needs the app on screen and the phone unlocked, or a jailbreak.
2. **A native Monero miner for iPhone exists, but it is unproven.** Its own
   README says JIT on a physical device "still needs validation".
3. **Browser miners need a server.** Pools speak plain TCP, which a web page
   cannot open, so each of these projects ships a proxy you must host.
4. **A script in a shell app is the only route with no computer, no signing
   and no server.** That is what `miner.py` in this folder does.

## Native miners that hash on the phone

| Repository | Stars | Last push | What it is | Verdict |
|---|---|---|---|---|
| [darkmaniac7/XMRAnd-Mobile](https://github.com/darkmaniac7/XMRAnd-Mobile) (read) | 0 | Oct 2026 | XMRig-based Monero app for Android and iOS, with an unsigned iOS build | The most complete. You must sign it yourself (its guide uses a Mac with Xcode), set up JIT with StikDebug and LocalDevVPN, and keep it on screen. Not validated on a real device by its author |
| [windowszhiwang/XMRig-for-iOS](https://github.com/windowszhiwang/XMRig-for-iOS) | 0 | Oct 2026 | XMRig for jailbroken iOS with a background service | Needs a jailbreak |
| [1640675651/xmrig_ios_jit_fix](https://github.com/1640675651/xmrig_ios_jit_fix) | 0 | Jun 2026 | No description | Not reviewed |
| [mikekazakov/xmr-stak-cpu-ios](https://github.com/mikekazakov/xmr-stak-cpu-ios) | 47 | 2018 | Old Monero miner port | From before Monero's current algorithm |
| [Luccifer/XMinerClient](https://github.com/Luccifer/XMinerClient) | 22 | 2020 | Old Monero miner | Not updated since 2020 |

## Tools the native route depends on

| Repository | Stars | What it does |
|---|---|---|
| [StikDebug/StikDebug](https://github.com/StikDebug/StikDebug) | 3,158 | Turns on JIT for a sideloaded app, on the phone, iOS 17.4 and later |
| [FrizzleM/SideInstaller](https://github.com/FrizzleM/SideInstaller) (read) | 1,446 | Installs SideStore on the phone itself. Its README says no computer is needed on iOS 27 or later; iOS 26.4 to 26.7 still needs a pairing file made on a computer. It asks for your Apple account password |
| [nab138/iloader](https://github.com/nab138/iloader) | 4,978 | Sideloading from a computer |
| [claration/impactor](https://github.com/claration/impactor) | 3,881 | Sideloading from a computer |
| [CelloSerenity/iOS-26-Sideloading-and-JIT-Complete-Walkthrough](https://github.com/CelloSerenity/iOS-26-Sideloading-and-JIT-Complete-Walkthrough) | 38 | A written guide to SideStore, LiveContainer and StikDebug |

## Browser miners

| Repository | Stars | Last push | Notes |
|---|---|---|---|
| [aa022/RandomX-bonanza](https://github.com/aa022/RandomX-bonanza) (read) | 17 | Oct 2026 | Monero in the browser at up to about 24% of native speed. Needs its proxy server, and HTTPS for Safari |
| [l1mey112/randomx.js](https://github.com/l1mey112/randomx.js) | 48 | Jan 2026 | Monero's algorithm in JavaScript |
| [PiTi2k5/Crypto-Webminer](https://github.com/PiTi2k5/Crypto-Webminer) | 230 | Oct 2026 | Web miner for older CryptoNight coins |
| [opd-ai/webminer](https://github.com/opd-ai/webminer) | 10 | Jun 2026 | Single-file Monero web miner that asks for consent |
| [notgiven688/webminerpool](https://github.com/notgiven688/webminerpool) | 270 | 2022 | Web miner plus its pool-side server |
| [tier2pool/stratum2websocket](https://github.com/tier2pool/stratum2websocket), [dungvn3000/luckyhash_proxy](https://github.com/dungvn3000/luckyhash_proxy) | 27, 0 | 2022, Aug 2026 | The bridge servers a browser miner needs |

Only RandomX-bonanza was read closely; the others are listed from their
descriptions.

## Lottery miners (the closest relatives of this rig)

| Repository | Stars | Last push | Notes |
|---|---|---|---|
| [BitMaker-hub/NerdMiner_v2](https://github.com/BitMaker-hub/NerdMiner_v2) (read) | 2,831 | Jun 2026 | Solo lottery miner for ESP32 boards. Its Stratum code was the reference for this rig's protocol, and its README is the source of the pool list |
| [03012009BTC/miners-bitcoin-lottery](https://github.com/03012009BTC/miners-bitcoin-lottery) (read) | 1 | Oct 2026 | Pure-Python solo miner with a dashboard; phones can join through a browser tab, but a PC must run the hub |
| [Unknown-brazilian/borderless-miner](https://github.com/Unknown-brazilian/borderless-miner) | 2 | Jun 2026 | Lottery miner app for old Android phones |
| [VolkanSah/Bitcoin-Lottery-Miner](https://github.com/VolkanSah/Bitcoin-Lottery-Miner), [vkThiago/NerdMinerPC](https://github.com/vkThiago/NerdMinerPC), [AlldDev/QuixoteMiner](https://github.com/AlldDev/QuixoteMiner) | 20, 12, 3 | 2024 to 2026 | Desktop Python lottery miners |

## Shell apps that can run the miner

| Repository | Stars | Notes |
|---|---|---|
| [holzschu/a-shell](https://github.com/holzschu/a-shell) (read) | 4,023 | Terminal for iOS with Python 3.13 built in. iOS suspends it when it leaves the screen |
| [ish-app/ish](https://github.com/ish-app/ish) (wiki read) | 20,732 | Linux shell that emulates an x86 processor. Its wiki documents staying alive in the background by reading `/dev/location` |

## Hive OS tools (monitoring only)

Hive OS itself runs on PCs with graphics cards and on ASIC machines, not on
phones. These projects only watch or control such rigs:
[redcap3000/hivectrl](https://github.com/redcap3000/hivectrl),
[kmarinaccio/hive-os-api-v2-stats](https://github.com/kmarinaccio/hive-os-api-v2-stats),
[wedsonregis/NiceHive](https://github.com/wedsonregis/NiceHive),
[v1ct0rv/hiveon-client](https://github.com/v1ct0rv/hiveon-client),
[y3tiCrypto/hiveos-local](https://github.com/y3tiCrypto/hiveos-local),
[minershive/hiveon-kb](https://github.com/minershive/hiveon-kb).
The ideas this rig borrows from Hive OS are listed in [README.md](README.md).

## Another coin: Duino-Coin

[duino-coin/duino-coin](https://github.com/duino-coin/duino-coin) (1,231
stars, pushed Nov 2026) is a coin designed for microcontrollers, with an
official web miner in
[duino-coin/duco-webservices](https://github.com/duino-coin/duco-webservices).
It would show a growing balance, which the Bitcoin lottery never does, but as
far as I know the coin is worth close to nothing. Not reviewed in depth and not built in.

## If you want small real earnings instead of a lottery

The only route is the native Monero miner (XMRAnd-Mobile) with JIT. On an
iPhone 17 Pro Max that means:

1. iOS 27 or later, to sideload without a computer through SideInstaller, or a
   computer for the pairing file on iOS 26.
2. Signing XMRAnd with your own Apple account. Its guide assumes a Mac with
   Xcode; whether SideStore alone can sign it with the entitlements its JIT
   helpers need is not something I could confirm.
3. StikDebug and LocalDevVPN for JIT, repeated after reboots and updates.
4. The app on screen and the phone unlocked the whole time.

Sideloading tools ask for your Apple account password. If you try this, use a
separate Apple account, not your main one. Earnings would be a few rupees a
day at best, going by mid-2026 Monero figures that I could not re-check here,
and none of the chain above has been tested on a real device by its authors
or by me.
