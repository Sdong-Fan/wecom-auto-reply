"""Non-blocking human-confirmation popup for three-tier dispatch.

Layout (vertical stacking, from brainstorming D1 choice A):
  ⏱ timer-bar | customer name | confidence %
  Customer message (gray, read-only)
  AI suggested reply (blue, editable Text widget)
  [Edit] [Send] [Reject] buttons

Communication with async main loop via asyncio.Queue.
"""

import asyncio
import logging
import tkinter as tk
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

POPUP_TIMEOUT_SECONDS = 30


@dataclass
class PopupResult:
    """Result returned from the confirmation popup."""
    action: str          # "send" | "edit_send" | "reject" | "timeout"
    edited_text: str = ""  # user-edited reply (for edit_send action)


def show_confirmation_popup(
    customer_name: str,
    customer_message: str,
    ai_reply: str,
    confidence: float,
    result_queue: asyncio.Queue,
) -> None:
    """Show a non-blocking tkinter confirmation popup.

    Runs on the tkinter main thread.  Must be called via
    ``root.after(0, lambda: show_confirmation_popup(...))`` from the
    async main loop.

    Thread-safety: all tkinter operations wrapped in try/except to
    prevent TclError crashes.

    Args:
        customer_name: OCR-extracted customer name.
        customer_message: The customer's original message.
        ai_reply: AI-generated suggested reply.
        confidence: Retrieval cosine score (0.0-1.0).
        result_queue: asyncio.Queue to put the PopupResult into.
    """
    try:
        _show(customer_name, customer_message, ai_reply, confidence, result_queue)
    except tk.TclError as e:
        logger.error(f"Popup TclError (already destroyed?): {e}")
        _fallback_to_queue(result_queue, PopupResult(action="timeout"))
    except RuntimeError as e:
        logger.error(f"Popup RuntimeError: {e}")
        _fallback_to_queue(result_queue, PopupResult(action="timeout"))


def _fallback_to_queue(q: asyncio.Queue, result: PopupResult):
    """Best-effort push to result queue when popup fails."""
    try:
        q.put_nowait(result)
    except asyncio.QueueFull:
        pass


def _show(customer_name: str,
          customer_message: str,
          ai_reply: str,
          confidence: float,
          result_queue: asyncio.Queue):
    """Internal implementation — creates and manages the Toplevel."""
    top = tk.Toplevel()
    top.title("确认回复")
    top.attributes("-topmost", True)
    top.resizable(False, False)

    # ── size and position ──────────────────────────────────────────

    win_w, win_h = 480, 360
    screen_w = top.winfo_screenwidth()
    screen_h = top.winfo_screenheight()
    x = (screen_w - win_w) // 2
    y = (screen_h - win_h) // 2
    top.geometry(f"{win_w}x{win_h}+{x}+{y}")

    result = PopupResult(action="timeout")
    remaining = POPUP_TIMEOUT_SECONDS

    # ── header ─────────────────────────────────────────────────────

    header = tk.Frame(top)
    header.pack(fill=tk.X, padx=10, pady=(10, 5))

    timer_label = tk.Label(
        header, text=f"⏱ 还剩 {remaining} 秒",
        font=("Microsoft YaHei", 10), fg="#666")
    timer_label.pack(side=tk.LEFT)

    info_label = tk.Label(
        header,
        text=f"客户: {customer_name} | 置信度: {confidence:.0%}",
        font=("Microsoft YaHei", 9), fg="#888")
    info_label.pack(side=tk.RIGHT)

    # ── customer message ───────────────────────────────────────────

    cust_frame = tk.LabelFrame(top, text="客户消息", padx=8, pady=5)
    cust_frame.pack(fill=tk.X, padx=10, pady=(5, 5))

    cust_text = tk.Text(cust_frame, height=3, wrap=tk.WORD, font=("Microsoft YaHei", 10),
                        state=tk.DISABLED, bg="#f5f5f5")
    cust_text.pack(fill=tk.X)
    if customer_message:
        cust_text.configure(state=tk.NORMAL)
        cust_text.insert("1.0", customer_message)
        cust_text.configure(state=tk.DISABLED)

    # ── AI reply (editable) ────────────────────────────────────────

    ai_frame = tk.LabelFrame(top, text="AI 建议回复 (可直接编辑)", padx=8, pady=5)
    ai_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(5, 5))

    ai_text = tk.Text(ai_frame, height=5, wrap=tk.WORD, font=("Microsoft YaHei", 10),
                      bg="#e3f2fd")
    ai_text.pack(fill=tk.BOTH, expand=True)
    if ai_reply:
        ai_text.insert("1.0", ai_reply)

    # ── action callbacks ───────────────────────────────────────────

    def do_send(edited: bool = False):
        nonlocal result
        final_text = ai_text.get("1.0", "end-1c").strip()
        action = "edit_send" if edited else "send"
        result = PopupResult(action=action, edited_text=final_text)
        _finish(top, result_queue, result)

    def do_reject():
        nonlocal result
        result = PopupResult(action="reject")
        _finish(top, result_queue, result)

    top.protocol("WM_DELETE_WINDOW", do_reject)

    # ── buttons ────────────────────────────────────────────────────

    btn_frame = tk.Frame(top)
    btn_frame.pack(fill=tk.X, padx=10, pady=(5, 10))

    tk.Button(btn_frame, text="编辑", bg="#e0e0e0",
              command=lambda: [ai_text.focus_set()]).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
    tk.Button(btn_frame, text="直接发送", bg="#1976d2", fg="white",
              command=lambda: do_send(edited=False)).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)
    tk.Button(btn_frame, text="拒绝", bg="#f44336", fg="white",
              command=do_reject).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=2)

    # ── countdown timer ────────────────────────────────────────────

    def update_timer():
        nonlocal remaining
        remaining -= 1
        if remaining > 0:
            try:
                timer_label.configure(text=f"⏱ 还剩 {remaining} 秒")
                timer_label.after(1000, update_timer)
            except tk.TclError:
                pass  # window already destroyed
        else:
            try:
                timer_label.configure(text="⏱ 超时 — 已进入待处理队列")
                top.after(500, lambda: _finish(top, result_queue, result))
            except tk.TclError:
                _fallback_to_queue(result_queue, result)

    top.after(1000, update_timer)
    top.focus_force()
    ai_text.focus_set()


def _finish(top: tk.Toplevel, q: asyncio.Queue, result: PopupResult):
    """Push result and destroy popup."""
    try:
        try:
            q.put_nowait(result)
        except asyncio.QueueFull:
            pass
    finally:
        try:
            top.destroy()
        except tk.TclError:
            pass
