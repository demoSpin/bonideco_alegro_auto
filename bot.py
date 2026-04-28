"""
Allegro marketplace Telegram bot.

Run:
    python bot.py
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Callable

from loguru import logger
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from allegro_api import AllegroClient, DatadomeBlocked, SessionExpired
from bot_state import (
    append_completed_run,
    format_state_summary,
    load_state,
    update_cookies_meta,
)
from config import (
    LOGS_DIR,
    OUTPUT_DIR,
    ROW_DELAY_MAX,
    ROW_DELAY_MIN,
    RUNS_DIR,
    STORAGE_DIR,
    STORAGE_STATE_PATH,
    TELEGRAM_ADMIN_IDS,
    TELEGRAM_BOT_TOKEN,
)
from import_cookies import import_cookies
from runner import RunController, list_runs, load_run_meta
from sheets import (
    SheetData,
    SheetNotPublicError,
    SheetParseError,
    fetch_sheet,
)


HEALTH_CHECK_SKU = "STR141C01"


# Persistent reply keyboard at the bottom of the chat — quick taps for common
# commands. Slash commands still work in parallel.
MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        [KeyboardButton("▶ Run"), KeyboardButton("📋 Status")],
        [KeyboardButton("🍪 Upload Cookies"), KeyboardButton("🔍 Health")],
        [KeyboardButton("📁 Past Runs"), KeyboardButton("⏹ Stop Run")],
    ],
    resize_keyboard=True,
    is_persistent=True,
    input_field_placeholder="Tap a button or type a command…",
)


def admin_only(func: Callable) -> Callable:
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user or user.id not in TELEGRAM_ADMIN_IDS:
            logger.warning(f"Unauthorized: user_id={user.id if user else '?'}")
            if update.message:
                await update.message.reply_text("⛔ Not authorized.")
            elif update.callback_query:
                await update.callback_query.answer("Not authorized.", show_alert=True)
            return
        return await func(update, context)

    return wrapper


# ---------- Basic commands ----------


@admin_only
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = load_state()
    text = (
        "👋 <b>Allegro Marketplace Bot</b>\n\n"
        f"{format_state_summary(state)}\n\n"
        "Use the buttons below, or type slash commands:\n"
        "/upload_cookies, /health, /run, /status,\n"
        "/runs, /download &lt;id&gt;, /cancel_run"
    )
    await update.message.reply_text(
        text, parse_mode="HTML", reply_markup=MAIN_KEYBOARD
    )


@admin_only
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_start(update, context)


@admin_only
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    active = context.application.bot_data.get("active_run")
    if active:
        meta = active["controller"].meta
        await update.message.reply_text(
            f"⏳ <b>Run {meta.id} in progress</b>\n"
            f"  Status: {meta.status}\n"
            f"  Mode: {meta.mode}\n"
            f"  Processed: {meta.processed}\n"
            f"  ✓ {meta.success}  − {meta.skipped}  ⚠ {meta.multi_variant}  ⛔ {meta.errors}\n"
            f"  Last good row: {meta.last_good_row}",
            parse_mode="HTML",
        )
        return

    state = load_state()
    await update.message.reply_text(format_state_summary(state))


@admin_only
async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.user_data.pop("run_state", None) is not None:
        context.user_data.pop("run_ctx", None)
        await update.message.reply_text("✗ Cancelled the /run dialog.")
        return
    await update.message.reply_text("Nothing pending. (For an active run use /cancel_run.)")


@admin_only
async def cmd_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = context.user_data.get("run_state")
    if not state:
        await update.message.reply_text("Nothing to go back to. Use /run to start.")
        return
    await _go_back_one_step(update, context)


async def _go_back_one_step(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Move the /run conversation one step earlier."""
    state = context.user_data.get("run_state")
    rc = context.user_data.setdefault("run_ctx", {})

    async def reply(text: str, **kwargs):
        if update.callback_query:
            await update.callback_query.message.reply_text(text, **kwargs)
        else:
            await update.message.reply_text(text, **kwargs)

    if state == "awaiting_url":
        context.user_data.pop("run_state", None)
        context.user_data.pop("run_ctx", None)
        await reply("✗ Cancelled (was at first step).")
        return

    if state == "awaiting_from":
        rc.pop("sheet_data", None)
        rc.pop("sheet_url", None)
        context.user_data["run_state"] = "awaiting_url"
        await reply(
            "↩ Back. Send the Google Sheets URL.\n\n/cancel to abort."
        )
        return

    if state == "awaiting_to":
        rc.pop("from_row", None)
        context.user_data["run_state"] = "awaiting_from"
        sheet_data = rc.get("sheet_data")
        n = len(sheet_data.rows) if sheet_data else 0
        await reply(
            f"↩ Back. From which row? (1..{n})\n"
            f"Send a number, or 'all'.\n\n/back, /cancel"
        )
        return

    if state == "awaiting_mode":
        rc.pop("mode", None)
        if rc.get("from_row") and rc.get("to_row") and rc.get("to_row") > rc.get("from_row", 0) - 1:
            context.user_data["run_state"] = "awaiting_to"
            sheet_data = rc.get("sheet_data")
            n = len(sheet_data.rows) if sheet_data else 0
            from_row = rc["from_row"]
            rc.pop("to_row", None)
            await reply(
                f"↩ Back. Up to which row? ({from_row}..{n}) or 'end'.\n\n/back, /cancel"
            )
        else:
            context.user_data["run_state"] = "awaiting_from"
            sheet_data = rc.get("sheet_data")
            n = len(sheet_data.rows) if sheet_data else 0
            rc.pop("from_row", None)
            rc.pop("to_row", None)
            await reply(
                f"↩ Back. From which row? (1..{n})\n\n/back, /cancel"
            )
        return

    if state == "awaiting_confirm":
        rc.pop("mode", None)
        context.user_data["run_state"] = "awaiting_mode"
        await reply(
            "↩ Back. Choose mode:",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("📝 DRAFT", callback_data="mode:draft"),
                    InlineKeyboardButton("🚀 LIVE", callback_data="mode:live"),
                ],
                [InlineKeyboardButton("← Back", callback_data="back:step")],
            ]),
        )
        return


@admin_only
async def cmd_cancel_run(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    active = context.application.bot_data.get("active_run")
    if not active:
        await update.message.reply_text("No active run.")
        return
    active["controller"].request_cancel()
    await update.message.reply_text("✗ Cancellation requested. Will stop after current row.")


# ---------- Cookies upload ----------


@admin_only
async def cmd_upload_cookies(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data["awaiting"] = "cookies"
    await update.message.reply_text(
        "📨 Send the cookies JSON file exported from Cookie-Editor.\n\n"
        "Steps:\n"
        "1. Open salescenter.allegro.com in regular Chrome (logged in)\n"
        "2. Click Cookie-Editor → Export → JSON\n"
        "3. Send the file here\n\n"
        "/cancel to abort."
    )


@admin_only
async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    awaiting = context.user_data.get("awaiting")
    if awaiting != "cookies":
        await update.message.reply_text(
            "I'm not expecting a file. Run /upload_cookies first."
        )
        return

    doc = update.message.document
    if not doc:
        return

    if not (doc.file_name or "").lower().endswith(".json"):
        await update.message.reply_text("Expected a .json file.")
        return

    incoming = STORAGE_DIR / "cookies_import.json"
    file = await doc.get_file()
    await file.download_to_drive(incoming)
    logger.info(f"Cookies file received: {doc.file_name}")

    try:
        n = import_cookies(incoming, STORAGE_STATE_PATH)
    except SystemExit:
        await update.message.reply_text(
            "❌ Could not parse JSON. Make sure it's a Cookie-Editor export."
        )
        context.user_data.pop("awaiting", None)
        return

    with open(STORAGE_STATE_PATH, "r", encoding="utf-8") as f:
        cookies_list = json.load(f).get("cookies", [])
    names = [c["name"] for c in cookies_list]
    update_cookies_meta(count=n, names=names)
    context.user_data.pop("awaiting", None)

    has_dd = "datadome" in names
    has_wdctx = "wdctx" in names
    msg = (
        f"✅ Imported {n} cookies.\n"
        f"  datadome: {'✓' if has_dd else '✗ MISSING'}\n"
        f"  wdctx:    {'✓' if has_wdctx else '✗ MISSING'}\n\n"
    )
    msg += (
        "Run /health to verify they actually work."
        if has_dd and has_wdctx
        else "⚠️ Critical cookies missing. Re-export from logged-in salescenter session."
    )
    await update.message.reply_text(msg)


# ---------- Health check ----------


@admin_only
async def cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not STORAGE_STATE_PATH.exists():
        await update.message.reply_text("❌ No cookies. Run /upload_cookies first.")
        return

    msg = await update.message.reply_text(f"⏳ Testing search for {HEALTH_CHECK_SKU}...")
    try:
        async with AllegroClient() as client:
            result = await client.search(HEALTH_CHECK_SKU)
        await msg.edit_text(
            f"✅ Cookies work.\n"
            f"  Test SKU: {HEALTH_CHECK_SKU}\n"
            f"  Result: {result.status}"
        )
    except DatadomeBlocked as e:
        await msg.edit_text(f"⛔ Datadome blocked.\n{e}\n\nRe-upload cookies.")
    except SessionExpired as e:
        await msg.edit_text(f"⛔ Session expired.\n{e}\n\nRe-upload cookies.")
    except Exception as e:
        await msg.edit_text(f"❌ Unexpected:\n{e}")


# ---------- /run conversation ----------


@admin_only
async def cmd_run(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.application.bot_data.get("active_run"):
        await update.message.reply_text(
            "⚠️ A run is already active. Use /status or /cancel_run."
        )
        return

    if not STORAGE_STATE_PATH.exists():
        await update.message.reply_text(
            "❌ No cookies loaded. Run /upload_cookies first."
        )
        return

    context.user_data["run_state"] = "awaiting_url"
    context.user_data["run_ctx"] = {}
    await update.message.reply_text(
        "📋 Send me the Google Sheets URL.\n\n"
        "<b>Important:</b> the sheet must be shared as "
        "<i>Anyone with the link can view</i>.\n\n"
        "Tip: /back at any step to redo the previous question, /cancel to abort.",
        parse_mode="HTML",
    )


async def _on_run_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = context.user_data.get("run_state")
    text = (update.message.text or "").strip()
    rc = context.user_data.setdefault("run_ctx", {})

    if state == "awaiting_url":
        await update.message.reply_text("⏳ Fetching sheet...")
        try:
            sheet_data = await fetch_sheet(text)
        except SheetNotPublicError as e:
            await update.message.reply_text(
                f"⛔ {e}\n\nFix sharing and resend the URL, or /cancel."
            )
            return
        except (ValueError, SheetParseError) as e:
            await update.message.reply_text(
                f"⛔ {e}\n\nResend a valid URL, or /cancel."
            )
            return
        except Exception as e:
            await update.message.reply_text(f"❌ Fetch failed: {e}")
            return

        if not sheet_data.rows:
            await update.message.reply_text(
                "⛔ No data rows found. Check the sheet has SKU/Price/Stock columns."
            )
            return

        rc["sheet_data"] = sheet_data
        rc["sheet_url"] = text
        context.user_data["run_state"] = "awaiting_from"
        n = len(sheet_data.rows)
        await update.message.reply_text(
            f"✓ Sheet loaded.\n"
            f"  {n} data rows.\n"
            f"  First SKU: {sheet_data.rows[0].sku}\n"
            f"  Last SKU:  {sheet_data.rows[-1].sku}\n\n"
            f"From which row? (1..{n})\n"
            f"Send a number, or 'all' for full sheet.\n\n"
            f"/back, /cancel"
        )
        return

    if state == "awaiting_from":
        sheet_data: SheetData = rc["sheet_data"]
        n = len(sheet_data.rows)
        if text.lower() == "all":
            rc["from_row"] = 1
            rc["to_row"] = n
            sr = sheet_data.rows[0]
            await update.message.reply_text(
                f"Full range selected: 1..{n}\n"
                f"  First: row 1, SKU={sr.sku}, price={sr.price}, stock={sr.stock}\n\n"
                f"Mode?",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("📝 DRAFT", callback_data="mode:draft"),
                        InlineKeyboardButton("🚀 LIVE", callback_data="mode:live"),
                    ]
                ]),
            )
            context.user_data["run_state"] = "awaiting_mode"
            return

        try:
            from_row = int(text)
            if not 1 <= from_row <= n:
                raise ValueError
        except ValueError:
            await update.message.reply_text(f"Bad input. Send a number 1..{n} or 'all'.")
            return

        sr = sheet_data.rows[from_row - 1]
        rc["from_row"] = from_row
        context.user_data["run_state"] = "awaiting_to"
        await update.message.reply_text(
            f"Row {from_row}: SKU={sr.sku}, price={sr.price}, stock={sr.stock}\n\n"
            f"Up to which row? ({from_row}..{n})\n"
            f"Send a number, or 'end' for the last row.\n\n"
            f"/back to pick another starting row, /cancel"
        )
        return

    if state == "awaiting_to":
        sheet_data: SheetData = rc["sheet_data"]
        n = len(sheet_data.rows)
        from_row = rc["from_row"]
        if text.lower() == "end":
            to_row = n
        else:
            try:
                to_row = int(text)
                if not from_row <= to_row <= n:
                    raise ValueError
            except ValueError:
                await update.message.reply_text(
                    f"Bad input. Send a number {from_row}..{n} or 'end'."
                )
                return

        rc["to_row"] = to_row
        context.user_data["run_state"] = "awaiting_mode"
        count = to_row - from_row + 1
        avg_delay = (ROW_DELAY_MIN + ROW_DELAY_MAX) / 2
        eta_s = int(count * (3 + avg_delay))
        if eta_s >= 3600:
            eta = f"{eta_s // 3600}h {(eta_s % 3600) // 60}m"
        elif eta_s >= 60:
            eta = f"{eta_s // 60} min"
        else:
            eta = f"{eta_s} s"
        await update.message.reply_text(
            f"Range: {from_row}..{to_row} ({count} items, ETA ~{eta})\n\n"
            f"Mode?",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("📝 DRAFT", callback_data="mode:draft"),
                    InlineKeyboardButton("🚀 LIVE", callback_data="mode:live"),
                ],
                [InlineKeyboardButton("← Back", callback_data="back:step")],
            ]),
        )
        return

    await update.message.reply_text("Unexpected text. /cancel to start over.")


@admin_only
async def on_run_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    data = q.data or ""
    rc = context.user_data.get("run_ctx", {})
    state = context.user_data.get("run_state")

    if data.startswith("mode:") and state == "awaiting_mode":
        mode = data.split(":", 1)[1]
        rc["mode"] = mode
        sheet_data: SheetData = rc["sheet_data"]
        from_row = rc["from_row"]
        to_row = rc["to_row"]
        count = to_row - from_row + 1
        context.user_data["run_state"] = "awaiting_confirm"

        await q.edit_message_text(
            f"Confirm:\n"
            f"  Range: {from_row}..{to_row} ({count} items)\n"
            f"  Mode:  {mode.upper()}\n"
            + ("⚠️ LIVE will publish offers visible to buyers.\n" if mode == "live" else ""),
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("▶ Start", callback_data="confirm:yes"),
                    InlineKeyboardButton("✗ Cancel", callback_data="confirm:no"),
                ],
                [InlineKeyboardButton("← Back (change mode)", callback_data="back:step")],
            ]),
        )
        return

    if data == "back:step":
        await _go_back_one_step(update, context)
        return

    if data == "confirm:no":
        context.user_data.pop("run_state", None)
        context.user_data.pop("run_ctx", None)
        await q.edit_message_text("✗ Cancelled.")
        return

    if data == "confirm:yes" and state == "awaiting_confirm":
        sheet_data: SheetData = rc["sheet_data"]
        from_row = rc["from_row"]
        to_row = rc["to_row"]
        mode = rc["mode"]
        sheet_url = rc["sheet_url"]

        context.user_data.pop("run_state", None)
        context.user_data.pop("run_ctx", None)

        chat_id = q.message.chat_id

        async def progress_cb(msg: str) -> None:
            try:
                await context.bot.send_message(chat_id=chat_id, text=msg)
            except Exception as e:
                logger.warning(f"send_message failed: {e}")

        controller = RunController(
            sheet_rows=sheet_data.rows,
            from_row=from_row,
            to_row=to_row,
            mode=mode,
            sheet_url=sheet_url,
            progress_cb=progress_cb,
        )
        context.application.bot_data["active_run"] = {
            "controller": controller,
            "chat_id": chat_id,
        }
        await q.edit_message_text(
            f"▶ Run {controller.run_id} kicked off. Updates incoming."
        )

        async def _runner_task() -> None:
            try:
                meta = await controller.run()
                append_completed_run({
                    "id": meta.id,
                    "status": meta.status,
                    "mode": meta.mode,
                    "processed": meta.processed,
                    "success": meta.success,
                    "errors": meta.errors,
                    "started_at": meta.started_at,
                    "finished_at": meta.finished_at,
                })
                await _send_run_artifacts(context, chat_id, meta.id)
            finally:
                context.application.bot_data.pop("active_run", None)

        context.application.create_task(_runner_task())
        return


# ---------- Sending artifacts (Excel + log) ----------


async def _send_run_artifacts(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    run_id: str,
) -> None:
    meta = load_run_meta(run_id)
    if not meta:
        await context.bot.send_message(chat_id, f"Could not find run {run_id}")
        return

    excel_path = Path(meta.excel_path)
    log_path = Path(meta.log_path)

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Excel", callback_data=f"dl:excel:{run_id}"),
            InlineKeyboardButton("📋 Logs", callback_data=f"dl:log:{run_id}"),
        ]
    ])

    summary = (
        f"<b>Run {run_id}</b>\n"
        f"  Status:    {meta.status}\n"
        f"  Mode:      {meta.mode}\n"
        f"  Range:     {meta.from_row}..{meta.to_row}\n"
        f"  Processed: {meta.processed}\n"
        f"  ✓ Success:        {meta.success}\n"
        f"  − Skipped:        {meta.skipped}\n"
        f"  ⚠ Multi-variant:  {meta.multi_variant}\n"
        f"  ⛔ Errors:        {meta.errors}\n"
        f"  Last good row:    {meta.last_good_row}\n"
    )
    if meta.last_error:
        summary += f"  Last error: {meta.last_error[:200]}\n"
    summary += "\nDownload files:"

    await context.bot.send_message(
        chat_id, summary, parse_mode="HTML", reply_markup=keyboard
    )

    if excel_path.exists():
        try:
            await context.bot.send_document(chat_id, document=excel_path.open("rb"), filename=excel_path.name)
        except Exception as e:
            logger.warning(f"Send Excel failed: {e}")
    if log_path.exists():
        try:
            await context.bot.send_document(chat_id, document=log_path.open("rb"), filename=log_path.name)
        except Exception as e:
            logger.warning(f"Send log failed: {e}")


@admin_only
async def on_download_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    await q.answer()
    data = q.data or ""
    if not data.startswith("dl:"):
        return
    parts = data.split(":")
    if len(parts) != 3:
        return
    _, kind, run_id = parts
    meta = load_run_meta(run_id)
    if not meta:
        await q.message.reply_text(f"Run {run_id} not found.")
        return
    path = Path(meta.excel_path if kind == "excel" else meta.log_path)
    if not path.exists():
        await q.message.reply_text(f"File not found: {path.name}")
        return
    try:
        await context.bot.send_document(
            q.message.chat_id, document=path.open("rb"), filename=path.name
        )
    except Exception as e:
        await q.message.reply_text(f"Send failed: {e}")


# ---------- Past runs listing & download ----------


@admin_only
async def cmd_runs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runs = list_runs(limit=15)
    if not runs:
        await update.message.reply_text("No past runs.")
        return
    lines = ["<b>Past runs:</b>"]
    for m in runs:
        lines.append(
            f"  <code>{m.id}</code>  {m.status:12}  "
            f"✓{m.success} ⚠{m.errors}  "
            f"{m.from_row}..{m.to_row} {m.mode}"
        )
    lines.append("\nResend files: /download &lt;id&gt;")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


@admin_only
async def cmd_download(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /download <run_id>\nSee /runs for IDs.")
        return
    run_id = args[0]
    meta = load_run_meta(run_id)
    if not meta:
        await update.message.reply_text(f"Run {run_id} not found.")
        return
    await _send_run_artifacts(context, update.effective_chat.id, run_id)


# ---------- Catch-all ----------


async def on_unknown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user or user.id not in TELEGRAM_ADMIN_IDS:
        return
    await update.message.reply_text("Unknown command. /start for menu.")


BUTTON_HANDLERS: dict[str, Callable] = {}


def _register_button_handlers() -> None:
    BUTTON_HANDLERS.update({
        "▶ Run": cmd_run,
        "📋 Status": cmd_status,
        "🍪 Upload Cookies": cmd_upload_cookies,
        "🔍 Health": cmd_health,
        "📁 Past Runs": cmd_runs,
        "⏹ Stop Run": cmd_cancel_run,
    })


@admin_only
async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (update.message.text or "").strip()

    # Reply-keyboard button presses come in as plain text — dispatch before
    # treating it as a /run conversation input.
    handler = BUTTON_HANDLERS.get(text)
    if handler:
        await handler(update, context)
        return

    if context.user_data.get("run_state"):
        await _on_run_text(update, context)
    else:
        await update.message.reply_text(
            "Tap a button or use /start.",
            reply_markup=MAIN_KEYBOARD,
        )


# ---------- App boot ----------


def build_app() -> Application:
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN not set in .env")
        sys.exit(1)
    if not TELEGRAM_ADMIN_IDS:
        logger.error("TELEGRAM_ADMIN_IDS not set in .env")
        sys.exit(1)

    logger.info(f"Starting bot. Authorized admins: {sorted(TELEGRAM_ADMIN_IDS)}")
    _register_button_handlers()
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("upload_cookies", cmd_upload_cookies))
    app.add_handler(CommandHandler("health", cmd_health))
    app.add_handler(CommandHandler("run", cmd_run))
    app.add_handler(CommandHandler("back", cmd_back))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("cancel_run", cmd_cancel_run))
    app.add_handler(CommandHandler("runs", cmd_runs))
    app.add_handler(CommandHandler("download", cmd_download))

    app.add_handler(CallbackQueryHandler(on_run_button, pattern=r"^(mode:|confirm:|back:)"))
    app.add_handler(CallbackQueryHandler(on_download_button, pattern=r"^dl:"))

    app.add_handler(MessageHandler(filters.Document.ALL, on_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(MessageHandler(filters.COMMAND, on_unknown))

    return app


def main() -> None:
    logger.add(
        LOGS_DIR / "bot_{time:YYYYMMDD}.log",
        rotation="00:00",
        retention="30 days",
        level="DEBUG",
    )
    app = build_app()
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
