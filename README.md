# linux-notify

Native Linux desktop notifications for [Hermes Agent](https://github.com/NousResearch/hermes-agent).
You get a notification when Hermes is blocked waiting for you, and when a long task finishes.
It works with any freedesktop.org notification daemon (mako, dunst, swaync, quickshell,
GNOME Shell, KDE Plasma, and others). It was built and tested on Omarchy (Hyprland on Arch).

| Event | Title | Urgency |
|---|---|---|
| Approval prompt | Hermes needs approval | critical (stays until dismissed) |
| Clarify question | Hermes has a question | critical |
| Sudo password prompt | Hermes needs your sudo password | critical |
| Turn finished (≥ 30 s) | Hermes: task complete — "Finished in 2m 14s · cli" | normal |
| Turn failed or cut off (≥ 30 s) | Hermes: task stopped — includes the exit reason | normal |

When you answer a prompt, its notification closes by itself, wherever you answered it
(terminal, Desktop, Telegram, and so on).

## What it does and doesn't do

- Notifications are sent only on the local D-Bus session bus. The plugin makes no
  network calls and has no telemetry.
- It runs `notify-send` and, to close a notification, `gdbus`. Both are called as an
  argument list (no shell) on a short-lived daemon thread with a timeout, so a slow
  or missing notification daemon never blocks the agent.
- Notification text comes from the prompt Hermes passes to `on_human_input_request`.
  Hermes force-redacts that prompt before any plugin sees it. The plugin never sees
  what you type (passwords and answers). It does not use `pre_approval_request`,
  which carries the unredacted command.
- It never shows the model's replies. A completion notification contains only the
  duration, the platform name and, for failures, Hermes's exit reason.
- It reads no files, writes nothing to disk, and stores no credentials. Its only
  state is in memory: turn start times, subagent session ids and open notification ids.
- It observes only. It never answers, approves or denies anything.
- Subagent turns never notify. Cron runs are skipped by default (`skip_platforms`).
  User interrupts (Ctrl-C, `/stop`) never notify.

Note: `hermes plugins validate` reports a `caution` finding, `sudo_usage`. This comes
from the literal notification title "Hermes needs your sudo password". The plugin
never runs sudo.

## Requirements

- Linux (or another OS) with a freedesktop notification daemon on the session bus.
- `notify-send` from libnotify:
  - Arch: `pacman -S libnotify`
  - Debian/Ubuntu: `apt install libnotify-bin`
  - Fedora: `dnf install libnotify`
- Optional: `gdbus` (from glib2, usually already installed). Without it,
  notifications still appear but don't close themselves when you answer the prompt.
- Hermes Agent 0.21.6 or newer.

The gateway also works when it runs as a systemd user service. If
`DBUS_SESSION_BUS_ADDRESS` isn't set, the plugin falls back to
`unix:path=/run/user/<uid>/bus`. As a result, prompts from Telegram, Discord or other
gateway sessions also notify the desktop.

## Install

```bash
hermes plugins install linux-notify          # from the catalog, once listed
# or straight from the repository:
hermes plugins install https://github.com/<owner>/hermes-linux-notify
hermes plugins enable linux-notify
```

The running gateway reloads plugin hooks automatically. For CLI sessions, start a new
`hermes` session.

To try it without waiting for a long task:

```bash
hermes config set plugins.entries.linux-notify.settings.completion_min_seconds 0
hermes chat -q "reply with just: ok"           # you should get "Hermes: task complete"
hermes config set plugins.entries.linux-notify.settings.completion_min_seconds 30
```

## Configuration

Settings live under `plugins.entries.linux-notify.settings` in `config.yaml`.
Set them with `hermes config set plugins.entries.linux-notify.settings.<key> <value>`.

| Key | Type | Default | Meaning |
|---|---|---|---|
| `notify_input` | bool | `true` | Notify when Hermes blocks on you (approval, clarify question, sudo password). |
| `notify_completion` | bool | `true` | Notify when a turn finishes. |
| `notify_failures` | bool | `true` | Also notify when a long turn fails or is cut off. User interrupts never notify. |
| `completion_min_seconds` | int | `30` | Only notify about turns that ran at least this long. `0` notifies on every turn. |
| `input_urgency` | str | `critical` | Urgency for prompt notifications: `low`, `normal` or `critical`. Most daemons keep critical notifications until you dismiss them. |
| `close_on_resolve` | bool | `true` | Dismiss the prompt notification once it has been answered. |
| `skip_platforms` | list | `[cron]` | Session platforms that never notify, e.g. `[cron, telegram]`. |
| `icon` | str | `hermes-desktop` | Icon name from your icon theme, or an absolute path to an image. |

## Troubleshooting

- **Nothing appears.** Run `notify-send test hello` in the same environment Hermes runs
  in. If that fails, the problem is the notification daemon or the D-Bus session, not
  the plugin. Also check `hermes plugins list` shows `linux-notify` as `enabled`.
- **No completion notifications.** Short turns are filtered out on purpose. Lower
  `completion_min_seconds`.
- **No icon.** libnotify 0.8 sends the icon as the `image-path` hint and leaves
  `app_icon` empty. That is normal. If your theme has no `hermes-desktop` icon, set
  `icon` to an absolute path or a stock name such as `dialog-information`.
- **Prompt notifications don't close.** Install `gdbus` (glib2). Some daemons also
  ignore `CloseNotification` for notifications that are already in their history.

## Hooks used

`pre_llm_call`, `on_session_end`, `subagent_start`, `on_human_input_request`,
`on_human_input_resolved`. All of them are observers: the plugin returns nothing that
changes the agent's behaviour.

## Development

```bash
python -m unittest discover -s tests -v                 # unit tests, no D-Bus needed
hermes plugins validate . --install-deps                # the same check catalog CI runs
```

## License

MIT. See [LICENSE](LICENSE).
