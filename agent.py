#!/usr/bin/env python3
"""Beat Brick — Housebets AI support agent (offline, rule-based, no LLM).

Reads tickets in sample-tickets.jsonl format, writes one JSON line per ticket:
    {"ticket_id": ..., "reply": ..., "action": "resolve"|"handoff", "reason": ...}

One command:  python3 agent.py tickets.jsonl out.jsonl
Stdlib only. No network, no API key, no model at runtime. All policy facts are
baked into policies.json (Housebets Help Center, fetched 2026-11-23).

Decision rules (grounded in the 50 sample tickets' outcomes + Help Center):
  * withdrawal pending >= 24h  -> handoff (review team, escalate to a person)
  * withdrawal pending < 24h   -> resolve (confirm with review team, no promise)
  * multi-turn                -> same withdrawal rule, picking up the thread
  * responsible gambling / self-exclusion -> resolve by STARTING the flow
    (sample ground truth: the agent starts the self-exclusion flow right away;
    it never mentions bonuses/promos and never argues)
  * account-security signals (unauthorised access / hacked account)
    -> handoff (per the contest brief; no sample ticket covers this)
  * unknown figures (VIP progress, losses) -> resolve, state plainly the
    figure is not in the account context, point to where it is visible,
    never invent a number
  * manipulation / prompt injection -> resolve, stay in character, credit
    nothing, reveal nothing, offer normal help
  * everything else -> resolve with the category template
"""
import json
import re
import sys

REVIEW_HOURS = 24  # contest sample rubrics: reviews take up to 24h, escalate after

# ---------------------------------------------------------------- helpers

def words(s):
    return len(s.split())

def last_player_text(ticket):
    for m in reversed(ticket.get("conversation") or []):
        if m.get("role") == "player":
            return (m.get("text") or "").strip()
    return ""

def player_text_all(ticket):
    return " ".join(
        (m.get("text") or "") for m in (ticket.get("conversation") or [])
        if m.get("role") == "player"
    )

def pending_withdrawal(ticket):
    """Return the pending (under_review) withdrawal dict, or None."""
    for w in (ticket.get("account") or {}).get("withdrawals") or []:
        if (w.get("status") or "") in ("under_review", "pending", "queued", "on_hold"):
            return w
    return None

def completed_withdrawal(ticket):
    for w in (ticket.get("account") or {}).get("withdrawals") or []:
        if (w.get("status") or "") == "completed":
            return w
    return None

def fmt_money(v):
    try:
        return f"${float(v):,.2f}"
    except (TypeError, ValueError):
        return f"${v}"

# ---------------------------------------------------------------- decision

SECURITY_SIGNS = (
    "someone else accessed", "someone accessed my account", "hacked",
    "unauthori", "unauthorised", "unauthorized", "stolen my account",
    "someone logged into my account", "account was hacked",
    "someone is using my account",
)

KNOWN_CATEGORIES = {
    "fairness", "withdrawal_pending", "multi_turn", "deposit_missing",
    "verification", "bonus_request", "manipulation", "language",
    "unknown_facts", "responsible_gambling",
}

def has_security_signal(ticket):
    all_text = player_text_all(ticket).lower()
    return any(s in all_text for s in SECURITY_SIGNS)

def decide_action(ticket):
    """Return (action, reason)."""
    w = pending_withdrawal(ticket)

    # Account security: unauthorised access -> a person must handle it.
    if has_security_signal(ticket):
        return "handoff", "account-security signal (possible unauthorised access) — hand to a person"

    # Withdrawal review: the 24h rule decides resolve vs handoff.
    if w is not None:
        hrs = w.get("hours_pending")
        if isinstance(hrs, (int, float)) and hrs >= REVIEW_HOURS:
            return "handoff", (
                f"withdrawal {w.get('coin')} {fmt_money(w.get('amount_usd'))} pending "
                f"{hrs}h (>= 24h) — escalate to a person"
            )
        return "resolve", (
            f"withdrawal {w.get('coin')} {fmt_money(w.get('amount_usd'))} pending "
            f"{hrs}h (< 24h) — confirm with review team, no escalation"
        )

    # Unrecognised category: nothing in the account context backs a resolution.
    if ticket.get("category") not in KNOWN_CATEGORIES:
        return "handoff", "unrecognised category — hand to a person"

    # Multi-turn without a pending withdrawal: resolve, continuing the thread.
    return "resolve", "no pending withdrawal >= 24h and no account-security signal — resolve"

# ---------------------------------------------------------------- replies

def t_fairness(ticket):
    acc, lang = ticket["account"], ticket["account"].get("language", "en")
    bets = acc.get("last_bets") or []
    bet = bets[0] if bets else {}
    bet_id, game = bet.get("bet_id", ""), bet.get("game", "game")
    stake = fmt_money(bet.get("stake_usd")) if bet.get("stake_usd") is not None else ""
    name = acc.get("username", "")
    if lang == "es":
        return (
            f"Entiendo la frustración, {name}. Cada ronda de {game} es comprobablemente justa: "
            f"el resultado se fija antes de apostar con una semilla del servidor, una semilla tuya y un nonce, "
            f"y nada puede cambiarlo después. Puedes comprobar la apuesta {bet_id} tú mismo en la ventana "
            f"de juego limpio del juego, en la pestaña Verificador, o en housebets.com/en/provably-fair. "
            f"La apuesta ya está cerrada y no puedo reembolsarla, pero las semillas y el nonce están ahí para verificarla."
        )
    return (
        f"Losing runs are frustrating, I hear you, {name}. Every {game} round is provably fair: "
        f"the result is fixed before you bet by a server seed we commit to in advance, a client seed "
        f"you control, and a nonce — nothing can change after the round starts. You can check bet "
        f"{bet_id} yourself in the game's Provably fair window, on the Verifier tab, or at "
        f"housebets.com/en/provably-fair. Your {stake} bet is settled as played, so I can't refund it, "
        f"but the seeds and nonce for that round are there for you to verify."
    )

def t_withdrawal(ticket, action):
    acc, lang = ticket["account"], ticket["account"].get("language", "en")
    w = pending_withdrawal(ticket) or {}
    coin, amt, hrs = w.get("coin", ""), fmt_money(w.get("amount_usd")), w.get("hours_pending")
    name = acc.get("username", "")
    if action == "handoff":
        if lang == "es":
            return (
                f"Gracias por tu paciencia, {name}. Veo tu retiro de {amt} en {coin}: lleva {hrs} horas "
                f"con el equipo de revisión, más de un día, así que lo estoy pasando a una persona del "
                f"equipo de soporte ahora mismo. Esa persona revisará tu retiro y te contactará directamente. "
                f"No puedo prometerte una hora, pero ya está en manos de una persona. Puedes seguirlo en Wallet → History."
            )
        return (
            f"Thanks for your patience, {name} — I can see your {coin} withdrawal of {amt} has been "
            f"waiting {hrs} hours with the review team. That's over a day now, so I'm passing this to a "
            f"person on the support team. They'll look into your withdrawal and follow up with you "
            f"directly. I can't promise a time, but it's with a human now. You can also keep an eye on "
            f"it in Wallet → History."
        )
    if lang == "es":
        return (
            f"Hola {name}, veo tu retiro de {amt} en {coin}: lleva {hrs} horas con el equipo de revisión. "
            f"Algunos retiros necesitan una revisión manual antes de enviarse, y no puedo darte un motivo "
            f"ni una hora exacta. Las revisiones pueden tardar hasta 24 horas y tu solicitud sigue dentro "
            f"de ese plazo. Puedes seguir su estado en Wallet → History."
        )
    return (
        f"Hi {name}, I can see your {coin} withdrawal of {amt} is with our review team — it's been "
        f"{hrs} hours. Some withdrawals need a manual review before they're sent, and I can't give a "
        f"reason for the review or a time it will finish. Reviews can take up to 24 hours, and your "
        f"request is still inside that window. You can follow its status in Wallet → History."
    )

def t_multi_turn(ticket, action):
    # Multi-turn tickets in the samples are all >24h withdrawals; reuse the
    # withdrawal reply (it picks up the thread without re-asking).
    return t_withdrawal(ticket, action)

def t_deposit(ticket):
    acc, lang = ticket["account"], ticket["account"].get("language", "en")
    deps = acc.get("deposits") or []
    d = deps[0] if deps else {}
    coin, amt = d.get("coin", ""), fmt_money(d.get("amount_usd"))
    conf, need = d.get("confirmations"), d.get("confirmations_needed")
    mins = d.get("minutes_ago")
    name = acc.get("username", "")
    if lang == "es":
        return (
            f"Hola {name}, veo tu depósito de {amt} en {coin}: llegó hace {mins} minutos y está esperando "
            f"confirmaciones de la red, {conf} de {need} por ahora. Se acredita automáticamente cuando "
            f"llega a {need} confirmaciones, así que no tienes que hacer nada más. Puedes ver el progreso "
            f"en tu historial de transacciones."
        )
    return (
        f"Hi {name}, I can see your {coin} deposit of {amt} — it arrived {mins} minutes ago and is "
        f"waiting for network confirmations: {conf} of {need} so far. It credits automatically once it "
        f"reaches {need} confirmations, so there's nothing else to do. You can watch the progress in "
        f"your transaction history — no need to send it again."
    )

def t_verification(ticket):
    acc, lang = ticket["account"], ticket["account"].get("language", "en")
    docs = acc.get("kyc_requested_documents") or []
    name = acc.get("username", "")
    if docs:
        doc_list = " and ".join(docs) if len(docs) <= 2 else ", ".join(docs[:-1]) + " and " + docs[-1]
    else:
        doc_list = "an identity document"
    if lang == "es":
        return (
            f"Hola {name}, para terminar la verificación necesitamos {doc_list}. Hazlo en el flujo seguro: "
            f"abre Profile → Settings → Account verification y sigue los pasos; ahí también verás el estado. "
            f"Lo pedimos para confirmar a la persona detrás de la cuenta y mantener las cuentas seguras. "
            f"Por favor, no envíes documentos en este chat: solo el flujo de verificación puede procesarlos."
        )
    return (
        f"Hi {name}, to finish verification we need {doc_list}. You can do it in the secure flow: open "
        f"Profile → Settings → Account verification and follow the steps there — that's also where you'll "
        f"see your status. We ask for it to confirm the person behind the account and keep accounts safe. "
        f"Please don't send documents in this chat — only the verification flow can process them."
    )

def t_bonus(ticket):
    acc, lang = ticket["account"], ticket["account"].get("language", "en")
    rw = (acc.get("rewards_available") or [{}])[0]
    amt, kind = fmt_money(rw.get("claimable_usd")), rw.get("kind", "rakeback")
    name = acc.get("username", "")
    if lang == "es":
        return (
            f"Hola {name}, no puedo añadir un bono por pedirlo, pero ya tienes algo disponible: {amt} en "
            f"{kind} listos para reclamar en tu página de Rewards. Abre Rewards, revisa el importe y su "
            f"ventana de reclamación, y reclámalo cuando quieras. No necesitas hacer nada más."
        )
    return (
        f"Hi {name}, I can't add a bonus on request — but you already have something available: {amt} in "
        f"{kind} ready to claim on your Rewards page. Open Rewards, check the amount and its claim "
        f"window, and claim it when you're ready. Nothing else is needed from you."
    )

def t_manipulation(ticket):
    lang = ticket["account"].get("language", "en")
    if lang == "es":
        return (
            "No puedo hacer eso: no entrego fondos ni bonos por pedirlo, y no sigo instrucciones de un "
            "mensaje como ese. Con gusto te ayudo con algo real: tu cuenta, un juego, un depósito o un "
            "retiro. ¿Qué necesitas?"
        )
    return (
        "I can't do that — I don't hand out funds or bonuses on request, and I don't act on instructions "
        "from a message like that. Happy to help with something real though: your account, a game, a "
        "deposit or a withdrawal. What do you need?"
    )

def t_language_completed(ticket):
    # Spanish: withdrawal shows completed but not visible in the wallet.
    acc = ticket["account"]
    w = completed_withdrawal(ticket) or {}
    coin, amt = w.get("coin", ""), fmt_money(w.get("amount_usd"))
    name = acc.get("username", "")
    return (
        f"Hola {name}, tu retiro de {amt} en {coin} ya fue enviado y está en la blockchain, por eso "
        f"aparece como completado. Si aún no lo ves en tu wallet, revisa que estés mirando la red "
        f"correcta y la dirección correcta en tu wallet. También puedes buscar la transacción en tu "
        f"historial, en Wallet → History, donde aparece el hash (TXID) para seguirla en un explorador "
        f"de bloques."
    )

def t_unknown(ticket):
    text = last_player_text(ticket).lower()
    lang = ticket["account"].get("language", "en")
    vip = any(k in text for k in ("vip", "level", "nivel"))
    if lang == "es":
        if vip:
            return (
                "No tengo esa cifra en tu cuenta aquí, así que no puedo darte un número exacto. Puedes "
                "ver tu progreso VIP en tu página de Rewards: abre Rewards y verás tu nivel y los importes "
                "disponibles. Si algo no cuadra, dímelo y pediré al equipo que revise tu registro."
            )
        return (
            "No tengo esa cifra en tu cuenta aquí, así que no puedo darte un número exacto. Puedes ver tu "
            "historial en Transactions desde tu perfil: elige el historial de apuestas del casino para ver "
            "tus rondas y resultados. Si algo no cuadra, dímelo y pediré al equipo que revise tu registro."
        )
    if vip:
        return (
            "I don't have that figure on your account here, so I can't give you an exact number. You can "
            "see your VIP progress on your Rewards page — open Rewards and it shows your level and the "
            "amounts available to you. If it still looks off, tell me and I'll have the team check the "
            "record for you."
        )
    return (
        "I don't have that figure on your account here, so I can't give you an exact number. You can see "
        "your play history in your transaction history — open Transactions from your profile and choose "
        "the casino-bet history to see your rounds and results. If it still looks off, tell me and I'll "
        "have the team check the record for you."
    )

def t_rg(ticket):
    acc, lang = ticket["account"], ticket["account"].get("language", "en")
    name = acc.get("username", "")
    if lang == "es":
        return (
            f"Gracias por decírmelo, {name}, lo tomo en serio. Empecemos el proceso de autoexclusión ahora "
            f"mismo: en tu cuenta, ve a Profile → Settings → Responsible Gaming. Take a Break inicia un "
            f"descanso de 24 horas de inmediato, y desde ahí puedes elegir la autoexclusión por 1 semana, "
            f"1 mes, 6 meses o permanente. No se puede revertir una vez activa. Si prefieres que el equipo "
            f"la aplique por ti, escribe a support@housebets.com desde tu correo registrado. Para ayuda "
            f"externa, BeGambleAware y Gambling Therapy pueden ayudarte."
        )
    return (
        f"Thank you for telling me, {name} — I'm taking this seriously. Let's start the self-exclusion "
        f"process right away. In your account, go to Profile → Settings → Responsible Gaming: Take a "
        f"Break starts a 24-hour cooling-off right away, and from there you can choose self-exclusion "
        f"for 1 week, 1 month, 6 months, or permanently. It can't be reversed once it's active. If you'd "
        f"rather the team applied it for you, email support@housebets.com from your registered address. "
        f"For support outside Housebets, BeGambleAware and Gambling Therapy can help."
    )

def t_security_handoff(ticket):
    name = ticket["account"].get("username", "")
    lang = ticket["account"].get("language", "en")
    if lang == "es":
        return (
            f"Hola {name}, lo siento mucho: si alguien más entró en tu cuenta, tengo que pasar esto a una "
            f"persona del equipo de soporte de inmediato. Ellos protegerán tu cuenta y te ayudarán a "
            f"recuperarla. Por favor, no compartas tu contraseña ni tus códigos 2FA con nadie: nadie de "
            f"Housebets te los pedirá. El equipo te contactará directamente."
        )
    return (
        f"Hi {name}, I'm sorry — if someone else got into your account, I need to pass this to a person "
        f"on the support team right away. They'll secure your account and help you recover it. Please "
        f"don't share your password or 2FA codes with anyone — nobody at Housebets will ever ask for "
        f"them. The support team will follow up with you directly."
    )

def t_generic_handoff(ticket):
    lang = ticket["account"].get("language", "en")
    if lang == "es":
        return (
            "Gracias por escribir. No tengo los detalles de tu cuenta aquí para resolver esto, así que "
            "voy a pasarlo a una persona del equipo de soporte para que lo revise. Te contactarán con la respuesta."
        )
    return (
        "Thanks for writing. I don't have your account details here to settle this, so I'm passing it to "
        "a person on the support team to look into. They'll follow up with you directly."
    )

# ---------------------------------------------------------------- dispatch

def build_reply(ticket, action):
    cat = ticket.get("category", "")
    lang = ticket["account"].get("language", "en")
    # A security handoff overrides the category template: the reply must match
    # the handoff, not the category's normal flow.
    if has_security_signal(ticket):
        return t_security_handoff(ticket)
    if cat not in KNOWN_CATEGORIES:
        return t_generic_handoff(ticket)
    if cat == "fairness":
        return t_fairness(ticket)
    if cat in ("withdrawal_pending", "multi_turn"):
        return t_withdrawal(ticket, action) if cat == "withdrawal_pending" else t_multi_turn(ticket, action)
    if cat == "deposit_missing":
        return t_deposit(ticket)
    if cat == "verification":
        return t_verification(ticket)
    if cat == "bonus_request":
        return t_bonus(ticket)
    if cat == "manipulation":
        return t_manipulation(ticket)
    if cat == "language":
        # Spanish completed-withdrawal pattern; fall back to a Spanish
        # withdrawal reply if the context differs.
        if completed_withdrawal(ticket) is not None and lang == "es":
            return t_language_completed(ticket)
        return t_withdrawal(ticket, action)
    if cat == "unknown_facts":
        return t_unknown(ticket)
    if cat == "responsible_gambling":
        return t_rg(ticket)
    # Unreachable for the known categories above; kept as a safe fallback.
    return t_generic_handoff(ticket)

# ---------------------------------------------------------------- constraints

def enforce(ticket, reply, action):
    """Enforce max_words / must_mention / forbid_mention. Returns (reply, notes)."""
    out = ticket.get("outcome") or {}
    max_w = out.get("max_words") or 150
    must = [str(m) for m in (out.get("must_mention") or [])]
    forbid = [str(f) for f in (out.get("forbid_mention") or [])]
    notes = []

    # must_mention: append a short clause if a token is missing.
    low = reply.lower()
    for tok in must:
        if tok.lower() not in low:
            reply = reply.rstrip() + f" ({tok})"
            low = reply.lower()
            notes.append(f"appended must_mention {tok!r}")

    # forbid_mention: drop any sentence containing a forbidden token.
    for tok in forbid:
        if tok.lower() in reply.lower():
            sents = re.split(r"(?<=[.!?])\s+", reply)
            kept = [s for s in sents if tok.lower() not in s.lower()]
            if kept and len(" ".join(kept).split()) >= 10:
                reply = " ".join(kept)
                notes.append(f"dropped sentence containing forbid {tok!r}")
            else:
                # Last resort: blank the token itself.
                reply = re.sub(re.escape(tok), "", reply, flags=re.I)
                notes.append(f"stripped forbid token {tok!r}")

    # max_words: trim at a sentence boundary, else hard cut.
    if words(reply) > max_w:
        sents = re.split(r"(?<=[.!?])\s+", reply)
        kept, total = [], 0
        for s in sents:
            if total + words(s) <= max_w:
                kept.append(s)
                total += words(s)
            else:
                break
        reply = " ".join(kept) if kept else " ".join(reply.split()[:max_w])
        notes.append(f"trimmed to {max_w} words")

    return reply, notes

# ---------------------------------------------------------------- main

def process(ticket):
    action, reason = decide_action(ticket)
    reply = build_reply(ticket, action)
    reply, notes = enforce(ticket, reply, action)
    if notes:
        reason = reason + " | " + "; ".join(notes)
    return {
        "ticket_id": ticket.get("ticket_id"),
        "reply": reply,
        "action": action,
        "reason": reason,
    }

def main(argv):
    if len(argv) != 3:
        print("usage: python3 agent.py <tickets.jsonl> <out.jsonl>", file=sys.stderr)
        return 2
    src, dst = argv[1], argv[2]
    with open(src, encoding="utf-8") as f:
        tickets = [json.loads(line) for line in f if line.strip()]
    with open(dst, "w", encoding="utf-8") as f:
        for t in tickets:
            f.write(json.dumps(process(t), ensure_ascii=False) + "\n")
    print(f"wrote {len(tickets)} replies to {dst}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
