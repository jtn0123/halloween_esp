# Your Halloween Castle

<!-- For the castle's owner: print this (about two pages). Lines marked
     TODO(screenshot) are where a picture belongs once the real screens
     have been captured; nothing below describes a screen that does not
     exist. Seller's notes are in docs/SUPPORT.md. -->

## Switching it on

Plug the castle's power supply into the wall. Give it about half a minute
before you try to reach it from a phone or computer. It starts quietly: the
show begins when you start it (or by itself, if you ask it to — see
**Picking a show**).

## Putting it on your Wi-Fi (the first time)

The castle does not know your Wi-Fi yet. Either way below works.

**With a phone.** Open your phone's Wi-Fi settings and join the network
called **Castle-** followed by four letters and numbers (for example
`Castle-3F2A`). It has no password. A page opens by itself: pick your home
Wi-Fi, type its password and save. The Castle- network disappears when the
castle has joined yours. If no page opens, open a browser on the phone and
go to `http://192.168.4.1`.
TODO(screenshot): the phone's Wi-Fi list, and the page that opens.

**With a computer and a USB-C cable.** In Google Chrome or Microsoft Edge,
open the castle's setup page, **https://jtn0123.github.io/halloween_esp/**,
plug the castle into the computer, click **Connect to the castle**, and
follow the Wi-Fi step it offers.
TODO(screenshot): the setup page and its Wi-Fi step.

## Opening the castle's page

On a phone or computer on the same Wi-Fi, open a browser and go to the
castle's name, **http://castle-xxxxxx.local** — the six characters are on
the label. If the name does not open, use the castle's number address from
the label, or find "castle" in your router's list of connected devices.
TODO(screenshot): the castle's page.

## Picking a show

<!-- Seller: this section, Factory reset and the castle key describe the
     castle's BUILT-IN page (firmware/sd_web_site.h kFallbackPage), which
     it serves when the card has no /site/ page. A card prepared with
     `sd_sync site` / `make publish` serves Castle Radio's page at / instead,
     and that page has no Settings or Factory reset. Check the shipped card
     before printing (docs/SUPPORT.md, "Before a castle leaves"). -->

The castle's page has one button for each show. Press one to start it and
**■ Stop** to stop it. Under **Settings**, tick **start the show at power-on**
if you want the castle to begin by itself every time it is switched on.

## The little light inside

There is a small light on the circuit board inside the castle.

| Light | Means |
| --- | --- |
| Blue | Starting up. |
| Orange | Installing an update. **Do not unplug it.** |
| Red | It cannot read its memory card (see below). |
| Off | All is well. |

## When something is wrong

1. **Switch it off and on.** Unplug it, count to ten, plug it back in. This
   fixes most things.
2. **The page will not open.** Check that your phone or computer is on your
   home Wi-Fi (not on mobile data, not on a guest network), then try again a
   minute after switching the castle on.
3. **New router, or a new Wi-Fi password.** Leave the castle on. After
   three minutes without its old network, the **Castle-** network comes back
   by itself: join it and set up the Wi-Fi again, as on the first day.
4. **Red light.** Switch the castle off, take the memory card out, push it
   back in until it clicks, and switch it on again.
5. **Still stuck.** Contact the person who sold you the castle and say what
   you see.

## Updating the castle

*In the Castle Tools app: coming in the next release.*

Until then, use the setup page with a USB-C cable, exactly as for Wi-Fi:
open **https://jtn0123.github.io/halloween_esp/** in Chrome or Edge, click
**Connect to the castle**, choose **Install**, and keep the cable plugged
in until it says it is done (about two minutes). Your songs on the memory
card are not touched. If it asks whether to erase the castle, erasing also
forgets your Wi-Fi: you will set the Wi-Fi up again at the end.

## Factory reset

On the castle's page, under **Settings**, press **Factory reset** and confirm.
The castle forgets your Wi-Fi, its key and its settings, restarts, and puts
up its **Castle-** network so you can set it up from the beginning. Your
songs on the memory card stay.

## The castle key

*Coming in the next release.*

## If it will not start at all

Use the setup page with a USB-C cable (see **Updating the castle**). If the
computer cannot find the castle: try another cable (some only charge) and
another USB port. Still nothing? On the castle's circuit board, **hold the
button marked BOOT, tap the button marked RESET, then let go of BOOT**, and
click **Connect to the castle** again.
TODO(screenshot): where BOOT and RESET are on the board.

## Castle Tools on your computer (adding songs)

Download it from **https://github.com/jtn0123/halloween_esp/releases/latest**:
the file ending `.dmg` for a Mac, or the one ending `-setup.exe` for Windows.
The app is not signed by Apple or Microsoft, so the very first time it opens,
your computer asks you to confirm.

- **Mac:** open the `.dmg` and drag **Castle Tools** into Applications, then
  open it. When the Mac says it cannot open it, go to **System Settings →
  Privacy & Security**, scroll down, and click **Open Anyway** next to the
  line about Castle Tools; confirm with your password.
  TODO(screenshot): the Privacy & Security pane with Open Anyway.
- **Windows:** run the `-setup.exe`. If a blue window says **Windows protected
  your PC**, click **More info**, then **Run anyway**.
  TODO(screenshot): the SmartScreen window, before and after More info.

You only do this once; the app updates itself after that.
