# RADIO: SDR scanner setup

The RADIO tab and its whole pipeline are already live — inbox sweep every 30 s,
CAS archiving, transcription, WATCH feeding. This document is the hardware half:
what to order, and how captures reach `data/radio/inbox/`.

**Why own hardware:** Broadcastify's TOS forbids automated capture. OpenMHz only
carries what volunteers feed it (thin CAL FIRE VHF coverage). A $40 receiver on
your own roof hears Butte County directly, and — the PSPS point — **local RF
needs no internet**. When the fiber goes down, this keeps working.

## 1. Shopping list (~$45–70)

| Item | Notes |
|---|---|
| **RTL-SDR Blog V4** dongle | ~$35. The standard. Buy from rtl-sdr.com's store or their Amazon listing — clones abound. |
| **VHF antenna** | The included dipole kit works to start. Better: a roof/attic-mounted quarter-wave ground plane cut for ~155 MHz, or a Nagoya NA-771 on a mag mount. Height beats everything. |
| USB extension (optional) | Gets the dongle away from PC RF noise. |

## 2. Frequencies — verify yourself, do not trust this file

CAL FIRE runs **conventional analog VHF** (not trunked), which is the easy case.
Look up the current Butte Unit (BTU) frequencies on RadioReference:

> radioreference.com → California → Butte County → CAL FIRE / Butte Unit

You want, typically: the **BTU Local net**, **Command** nets, and **Tactical**
channels (statewide CAL FIRE tacs sit in 151.1–151.4 MHz). Write down what
RadioReference lists TODAY — plans change, and this repo does not assert
frequencies as fact.

Butte County Sheriff / local fire may be on a trunked system; that is a later
upgrade (`trunk-recorder` instead of `rtl_airband`) and the inbox contract
already handles it.

## 3. Capture software: rtl_airband — DUAL output (archive + live)

`rtl_airband` scans a list of conventional channels with one dongle and can
write BOTH outputs at once, which is exactly the design:

- **file output** → the inbox → CAS archive, transcripts, WATCH (the record)
- **icecast output** → the stack's own icecast server → the RADIO tab's
  **"My SDR — LIVE (Butte CAL FIRE VHF)"** channel plays it in-app (the live
  scanner). Free forever, zero third-party terms, PSPS-proof.

The icecast server is already in the stack (`docker compose` service
`icecast`, loopback port **8100**; source password `toast-sdr-source` unless
overridden in `.env`). The RADIO channel is pre-seeded and pointed at
`http://localhost:8100/calfire-btu` — the moment rtl_airband connects, ▶
LISTEN goes live. Windows builds of rtl_airband exist; it also runs fine in
Docker or WSL. Config sketch (`rtl_airband.conf`):

```
devices: ({
  type = "rtlsdr";
  index = 0;
  gain = 32;
  centerfreq = 151300000;
  channels: (
    {
      freq = 151190000;            # EXAMPLE ONLY — use RadioReference's current list
      outputs: (
        {
          type = "file";
          directory = "F:/Cursor Projects/ToastReportOPs/data/radio/inbox";
          filename_template = "btu_local";
          split_on_transmission = true;   # one file per transmission — required
        },
        {
          type = "icecast";
          server = "127.0.0.1";
          port = 8100;
          mountpoint = "calfire-btu";     # must match the seeded channel URL
          username = "source";
          password = "toast-sdr-source";  # ICECAST_SOURCE_PASSWORD if overridden
          name = "BTU CAL FIRE VHF";
        }
      );
    }
    # ...one block per channel; all channels can share the one icecast mount
    # (a merged scan, like a real scanner) or use one mount per channel
  );
});
```

`split_on_transmission = true` is the line that matters for the archive: one
audio file per push-to-talk. The icecast output carries the same audio as a
continuous live stream.

**Test the live path without the SDR** (a 15-second tone through the real
mount — the seeded channel should play it in the RADIO tab):

```bash
docker run --rm linuxserver/ffmpeg -f lavfi -i sine=frequency=440 -t 15 \
  -c:a libmp3lame -b:a 48k -content_type audio/mpeg -f mp3 \
  icecast://source:toast-sdr-source@host.docker.internal:8100/calfire-btu
```

## 4. The inbox contract

Anything that drops audio here is ingested within 30 seconds:

```
data/radio/inbox/<anything>.wav|mp3|m4a|ogg     ← the transmission
data/radio/inbox/<same-name>.json               ← optional sidecar
```

Sidecar fields (all optional): `system`, `talkgroup`, `freq_hz`,
`start_time` (unix epoch), `duration_s`. No sidecar = timestamps from the file.
rtl_airband does not write sidecars; a 5-line wrapper script can, or skip it —
calls still ingest, just unlabelled.

## 5. Transcription (optional but the point)

Run any OpenAI-compatible Whisper server locally, e.g. `speaches`
(formerly faster-whisper-server):

```
docker run -p 8300:8000 ghcr.io/speaches-ai/speaches:latest-cpu
```

Then in `.env`: `WHISPER_URL=http://host.docker.internal:8300` and restart the
worker. Pending calls transcribe automatically; fire-worded transmissions feed
the WATCH board as T5 signals — quarantined from drafts and evacuation displays
by tier, like every community-grade source.

No endpoint configured = calls sit visibly `PENDING`. A transcript is never
fabricated.
