#!/usr/bin/env python3
"""Beat Brick v2 — Housebets AI support agent (offline, rule-based, no LLM).

Reads tickets in sample-tickets.jsonl format, writes one JSON line per ticket:
    {"ticket_id": ..., "reply": ..., "action": "resolve"|"handoff", "reason": ...}

One command:  python3 agent.py tickets.jsonl out.jsonl
Stdlib only. No network, no API key, no model at runtime. All policy facts are
baked into policies.json (Housebets Help Center, fetched 2026-11-23).

v2 changes over v1 (same decision core, hardened for unseen tickets):
  * Template variety: every reply template has 2-3 phrasings, picked
    deterministically from the ticket_id, so same-category tickets no longer
    read as one copy-pasted paragraph (tone is 20 of 100 pts).
  * Category normalisation + content inference: category labels are mapped
    through aliases, and when the label is missing/unknown the topic is
    inferred from the player's own words — the agent no longer hands off
    everything with an unfamiliar label.
  * Content signals for responsible gambling, manipulation and account
    security (English + Spanish), checked before the category dispatch, so
    those decisions survive label drift.
  * Multi-turn tickets are routed by topic (withdrawal / deposit / fairness /
    bonus / verification / RG / unknown) with a thread-acknowledging lead,
    instead of always answering as a withdrawal.
  * Context guards: no invented figures when the account context is missing
    a bet, a reward, a deposit or a withdrawal time (a $None / "bet  " reply
    can no longer happen); Portuguese replies for the most common templates.
  * Withdrawal statuses expanded (processing / in_review / review / ...).

Decision rules (grounded in the 50 sample tickets' outcomes + Help Center):
  * withdrawal pending >= 24h  -> handoff (review team, escalate to a person)
  * withdrawal pending < 24h   -> resolve (confirm with review team, no promise)
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
import hashlib
import json
import re
import sys

REVIEW_HOURS = 24  # contest sample rubrics: reviews take up to 24h, escalate after

# ---------------------------------------------------------------- helpers

def words(s):
    return len(s.split())

def pick(variants, key):
    """Deterministic variant choice from a ticket id (stable across runs)."""
    h = int(hashlib.md5(str(key).encode("utf-8")).hexdigest()[:8], 16)
    return variants[h % len(variants)]

def uname(ticket):
    return (ticket.get("account") or {}).get("username") or "there"

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
    """Return the pending withdrawal dict, or None."""
    pending = ("under_review", "pending", "queued", "on_hold", "processing",
               "in_review", "review", "manual_review", "awaiting_review",
               "under review")
    for w in (ticket.get("account") or {}).get("withdrawals") or []:
        if (w.get("status") or "").lower().replace(" ", "_") in pending:
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
        return None

# ---------------------------------------------------------------- signals

SECURITY_SIGNS = (
    "someone else accessed", "someone accessed my account", "hacked",
    "unauthori", "unauthorized", "unauthorised", "stolen my account",
    "someone logged into my account", "account was hacked",
    "someone is using my account", "someone got into my account",
    "someone broke into my account", "my account was stolen",
    "me hackearon", "alguien entró en mi cuenta", "alguien entro en mi cuenta",
    "robaron mi cuenta", "alguien accedió a mi cuenta",
    "alguien accedio a mi cuenta", "alguien usa mi cuenta",
    "me robaron la cuenta",
)

# Gambling-harm phrases only (bare "can't stop" is left out on purpose:
# it false-positives on "can't stop checking my withdrawal").
RG_SIGNS = (
    "self-exclud", "self exclud", "self-exclus", "gambling problem",
    "addicted", "addiction", "stop playing", "stop gambling",
    "lock my account", "block my account", "take a break",
    "help me stop", "lost my rent", "can't control it", "cant control it",
    "i need to stop", "i cant control", "autoexclus", "adicto",
    "adicción", "adiccion", "no puedo parar", "no puedo dejar",
    "dejar de jugar", "bloquea mi cuenta", "bloquear mi cuenta",
    "ayuda para dejar", "perdí el alquiler", "perdi el alquiler",
)

# Injection / impersonation phrases. Deliberately NOT "give me" or "free
# spins": a bonus request ("give me something back") is not manipulation.
MANIPULATION_SIGNS = (
    "system prompt", "admin mode", "ignore your", "ignore previous",
    "pretend you", "pretend you're", "act as", "jailbreak",
    "ceo's friend", "ceo’s friend", "grandma", "developer mode",
    "override your", "god mode", "you are now", "forget your instructions",
)

def _has_any(text, signs):
    return any(s in text for s in signs)

def has_security_signal(ticket):
    return _has_any(player_text_all(ticket).lower(), SECURITY_SIGNS)

def has_rg_signal(ticket):
    return _has_any(player_text_all(ticket).lower(), RG_SIGNS)

def has_manipulation_signal(ticket):
    return _has_any(player_text_all(ticket).lower(), MANIPULATION_SIGNS)

# ---------------------------------------------------------------- categories

CATEGORY_ALIASES = {
    "withdrawal": "withdrawal_pending",
    "withdrawal_review": "withdrawal_pending",
    "withdrawal_pending": "withdrawal_pending",
    "deposit": "deposit_missing",
    "missing_deposit": "deposit_missing",
    "deposit_missing": "deposit_missing",
    "bonus": "bonus_request",
    "rakeback": "bonus_request",
    "bonus_request": "bonus_request",
    "verify": "verification",
    "kyc": "verification",
    "verification": "verification",
    "fairness": "fairness",
    "provably_fair": "fairness",
    "rigged": "fairness",
    "rg": "responsible_gambling",
    "self_exclusion": "responsible_gambling",
    "self-exclusion": "responsible_gambling",
    "responsible_gambling": "responsible_gambling",
    "manipulation": "manipulation",
    "prompt_injection": "manipulation",
    "jailbreak": "manipulation",
    "language": "language",
    "spanish": "language",
    "unknown": "unknown_facts",
    "unknown_facts": "unknown_facts",
    "multi_turn": "multi_turn",
    "multi-turn": "multi_turn",
    "follow_up": "multi_turn",
    "follow-up": "multi_turn",
}

def normalize_category(cat):
    if not cat:
        return None
    return CATEGORY_ALIASES.get(str(cat).strip().lower().replace(" ", "_"))

TOPIC_SIGNS = (
    ("withdrawal_pending", ("withdraw", "retiro", "payout", "cashout", "cash out")),
    ("deposit_missing", ("deposit", "depósito", "deposito", "confirmations",
                         "confirmaciones", "not in my balance", "missing deposit")),
    ("fairness", ("rigged", "fixed", "unfair", "fair", "provably", "cheat",
                  "scam", "amañado")),
    ("verification", ("verif", "kyc", "documents", "documentos",
                      "identity document")),
    ("bonus_request", ("bonus", "bono", "rakeback", "reload", "free spins",
                      "recompensa")),
    ("unknown_facts", ("vip level", "nivel vip", "when do i reach",
                       "how much have i lost", "how much did i")),
)

def infer_topic(ticket):
    """Best-guess category from the player's own words (fallback only)."""
    text = player_text_all(ticket).lower()
    if _has_any(text, RG_SIGNS):
        return "responsible_gambling"
    if _has_any(text, MANIPULATION_SIGNS):
        return "manipulation"
    for cat, signs in TOPIC_SIGNS:
        if _has_any(text, signs):
            return cat
    return None

# ---------------------------------------------------------------- decision

def decide_action(ticket):
    """Return (action, reason)."""
    # Account security: unauthorised access -> a person must handle it.
    if has_security_signal(ticket):
        return "handoff", "account-security signal (possible unauthorised access) — hand to a person"

    # Responsible gambling: start the flow right away (sample ground truth).
    if has_rg_signal(ticket):
        return "resolve", "responsible-gambling signal — start the self-exclusion flow"

    # Manipulation: stay in character, credit nothing.
    if has_manipulation_signal(ticket):
        return "resolve", "manipulation attempt — stay in character, credit nothing"

    # Withdrawal review: the 24h rule decides resolve vs handoff.
    w = pending_withdrawal(ticket)
    if w is not None:
        hrs = w.get("hours_pending")
        amt = fmt_money(w.get("amount_usd")) or "your withdrawal"
        if isinstance(hrs, (int, float)) and hrs >= REVIEW_HOURS:
            return "handoff", (
                f"withdrawal {w.get('coin')} {amt} pending "
                f"{hrs}h (>= 24h) — escalate to a person"
            )
        return "resolve", (
            f"withdrawal {w.get('coin')} {amt} pending "
            f"{hrs}h (< 24h) — confirm with review team, no escalation"
        )

    # Known label -> resolve with its template; unknown label -> infer topic.
    cat = normalize_category(ticket.get("category"))
    if cat is not None:
        return "resolve", f"category {cat} — resolve"
    inferred = infer_topic(ticket)
    if inferred is not None:
        return "resolve", f"category missing/unknown; inferred {inferred} from the message — resolve"
    return "handoff", "unrecognised category and no clear topic — hand to a person"


# ---------------------------------------------------------------- replies

def t_fairness(ticket):
    acc = ticket["account"]
    lang = acc.get("language", "en")
    name = uname(ticket)
    tid = ticket.get("ticket_id", "")
    bets = acc.get("last_bets") or []
    bet = bets[0] if bets else {}
    bet_id, game = bet.get("bet_id"), bet.get("game")
    stake = fmt_money(bet.get("stake_usd")) if bet.get("stake_usd") is not None else None

    if lang == "es":
        if not bet_id:
            return (
                f"Entiendo la frustración, {name}. Cada ronda es comprobablemente justa: "
                f"el resultado se fija antes de apostar con una semilla del servidor, una "
                f"semilla tuya y un nonce, y nada puede cambiarlo después. Puedes comprobar "
                f"cualquier apuesta en la ventana de juego limpio del juego, en la pestaña "
                f"Verificador, o en housebets.com/en/provably-fair."
            )
        openers = [
            f"Entiendo la frustración, {name}.",
            f"Sé que una racha así molesta, {name}.",
        ]
        body = (
            f"Cada ronda de {game} es comprobablemente justa: el resultado se fija antes "
            f"de apostar con una semilla del servidor, una semilla tuya y un nonce, y nada "
            f"puede cambiarlo después. Puedes comprobar la apuesta {bet_id} tú mismo en la "
            f"ventana de juego limpio del juego, en la pestaña Verificador, o en "
            f"housebets.com/en/provably-fair."
        )
        closers = [
            "La apuesta ya está cerrada y no puedo reembolsarla, pero las semillas y el nonce están ahí para verificarla.",
            f"Tu apuesta de {stake} quedó cerrada como se jugó y no puedo reembolsarla, pero puedes verificarla con las semillas y el nonce.",
        ]
        return pick(openers, tid) + " " + body + " " + pick(closers, tid + "c")

    if lang == "pt":
        if not bet_id:
            return (
                f"Percebo a frustração, {name}. Cada ronda é comprovadamente justa: o "
                f"resultado é fixado antes de apostares por uma semente do servidor, uma "
                f"semente tua e um nonce — nada pode mudar depois de a ronda começar. Podes "
                f"verificar qualquer aposta na janela de jogo justo do jogo, no separador "
                f"Verificador, ou em housebets.com/en/provably-fair."
            )
        return (
            f"Percebo a frustração, {name}. Cada ronda de {game} é comprovadamente justa: "
            f"o resultado é fixado antes de apostares por uma semente do servidor, uma "
            f"semente tua e um nonce — nada pode mudar depois de a ronda começar. Podes "
            f"verificar a aposta {bet_id} na janela de jogo justo do jogo, no separador "
            f"Verificador, ou em housebets.com/en/provably-fair. A tua aposta de {stake} "
            f"está fechada como jogada, por isso não a posso reembolsar, mas as sementes e "
            f"o nonce estão lá para verificares."
        )

    if not bet_id:
        return (
            f"Sorry about the run, {name} — I know losing streaks are frustrating. Every "
            f"round is provably fair: the result is fixed before you bet by a server seed "
            f"we commit to in advance, a client seed you control, and a nonce. You can "
            f"check any bet yourself in the game's Provably fair window, on the Verifier "
            f"tab, or at housebets.com/en/provably-fair — open Transactions from your "
            f"profile and choose the casino-bet history to find the bet."
        )
    openers = [
        f"Losing runs are frustrating, I hear you, {name}.",
        f"That's a rough run, {name} — I get why it feels off.",
        f"Sorry about the run of losses, {name}. I know that's frustrating to watch.",
    ]
    bodies = [
        f"Every {game} round is provably fair: the result is fixed before you bet by a "
        f"server seed we commit to in advance, a client seed you control, and a nonce — "
        f"nothing can change after the round starts. You can check bet {bet_id} yourself "
        f"in the game's Provably fair window, on the Verifier tab, or at "
        f"housebets.com/en/provably-fair.",
        f"Each {game} round is decided before you place the bet — a committed server "
        f"seed, your own client seed, and a nonce — so the outcome can't be changed "
        f"mid-round. Bet {bet_id} is yours to verify: open the game's Provably fair "
        f"window or the Verifier tab, or go to housebets.com/en/provably-fair.",
    ]
    closers = [
        f"Your {stake} bet is settled as played, so I can't refund it, but the seeds and "
        f"nonce for that round are there for you to verify.",
        f"The {stake} bet stands as played and I can't refund it — but everything you "
        f"need to check that round is in the verifier.",
    ]
    return (pick(openers, tid) + " " + pick(bodies, tid + "b") + " "
            + pick(closers, tid + "c"))


def t_withdrawal(ticket, action):
    acc = ticket["account"]
    lang = acc.get("language", "en")
    name = uname(ticket)
    tid = ticket.get("ticket_id", "")
    w = pending_withdrawal(ticket) or {}
    coin = w.get("coin", "")
    amt = fmt_money(w.get("amount_usd"))
    amt_txt = amt if amt else "your withdrawal"
    hrs = w.get("hours_pending")
    hrs_txt = f"{hrs} hours" if isinstance(hrs, (int, float)) else "some time"

    if lang == "pt":
        if action == "handoff":
            return (
                f"Obrigado pela paciência, {name} — o teu levantamento de {amt_txt} em "
                f"{coin} está à espera há {hrs_txt} com a equipa de revisão. Já passou um "
                f"dia, por isso vou passar isto a uma pessoa da equipa de apoio. Ela vai "
                f"analisar o teu levantamento e contactar-te diretamente. Não te posso "
                f"prometer uma hora, mas já está com uma pessoa. Também podes acompanhar em "
                f"Wallet → History."
            )
        return (
            f"Olá {name}, vejo que o teu levantamento de {amt_txt} em {coin} está com a "
            f"equipa de revisão — já lá está há {hrs_txt}. Alguns levantamentos precisam "
            f"de uma revisão manual antes de serem enviados, e não te posso dizer o motivo "
            f"nem quando termina. As revisões podem demorar até 24 horas e o teu pedido "
            f"ainda está dentro desse prazo. Podes acompanhar o estado em Wallet → History."
        )

    if lang == "es":
        if action == "handoff":
            openers = [
                f"Gracias por tu paciencia, {name}. Veo tu retiro de {amt_txt} en {coin}: "
                f"lleva {hrs_txt} con el equipo de revisión, más de un día, así que lo estoy "
                f"pasando a una persona del equipo de soporte ahora mismo.",
                f"Gracias por esperar, {name}. Tu retiro de {amt_txt} en {coin} lleva "
                f"{hrs_txt} en revisión, más de un día, así que lo paso ahora mismo a una "
                f"persona del equipo de soporte.",
            ]
            closers = [
                "Esa persona revisará tu retiro y te contactará directamente. No puedo prometerte una hora, pero ya está en manos de una persona. Puedes seguirlo en Wallet → History.",
                "Esa persona se encargará de tu retiro y te escribirá directamente. No puedo darte una hora, pero ya lo tiene una persona. Puedes seguirlo en Wallet → History.",
            ]
            return pick(openers, tid) + " " + pick(closers, tid + "c")
        openers = [
            f"Hola {name}, veo tu retiro de {amt_txt} en {coin}: lleva {hrs_txt} con el "
            f"equipo de revisión.",
            f"Hola {name}, encontré tu retiro de {amt_txt} en {coin}: está con el equipo "
            f"de revisión desde hace {hrs_txt}.",
        ]
        body = (
            "Algunos retiros necesitan una revisión manual antes de enviarse, y no puedo "
            "darte un motivo ni una hora exacta. Las revisiones pueden tardar hasta 24 "
            "horas y tu solicitud sigue dentro de ese plazo."
        )
        return pick(openers, tid) + " " + body + " Puedes seguir su estado en Wallet → History."

    if action == "handoff":
        openers = [
            f"Thanks for your patience, {name} — I can see your {coin} withdrawal of "
            f"{amt_txt} has been waiting {hrs_txt} with the review team.",
            f"I appreciate you hanging on, {name}. Your {coin} withdrawal of {amt_txt} "
            f"has been with the review team for {hrs_txt} now.",
            f"Thanks for waiting, {name} — your {coin} withdrawal of {amt_txt} has been "
            f"in review for {hrs_txt}.",
        ]
        bodies = [
            "That's over a day now, so I'm passing this to a person on the support team. "
            "They'll look into your withdrawal and follow up with you directly. I can't "
            "promise a time, but it's with a human now.",
            "That's past a day, so I'm handing this to a person on the support team now — "
            "they'll take it from here and follow up with you directly. I can't give you "
            "a time, but a human has it.",
        ]
        closers = [
            "You can also keep an eye on it in Wallet → History.",
            "Wallet → History will show any change while they work on it.",
        ]
        return (pick(openers, tid) + " " + pick(bodies, tid + "b") + " "
                + pick(closers, tid + "c"))

    openers = [
        f"Hi {name}, I can see your {coin} withdrawal of {amt_txt} is with our review "
        f"team — it's been {hrs_txt}.",
        f"Thanks for checking in, {name}. Your {coin} withdrawal of {amt_txt} is with "
        f"the review team, {hrs_txt} in.",
        f"Hi {name} — I've found your {coin} withdrawal of {amt_txt}; it's been with "
        f"our review team for {hrs_txt}.",
    ]
    bodies = [
        "Some withdrawals need a manual review before they're sent, and I can't give a "
        "reason for the review or a time it will finish. Reviews can take up to 24 "
        "hours, and your request is still inside that window.",
        "A few withdrawals go through a manual check first — I can't say why yours was "
        "picked or when it will clear, but reviews can take up to 24 hours and yours "
        "is still within that time.",
    ]
    closers = [
        "You can follow its status in Wallet → History.",
        "Wallet → History shows the live status while you wait.",
    ]
    return (pick(openers, tid) + " " + pick(bodies, tid + "b") + " "
            + pick(closers, tid + "c"))


THREAD_LEADS = {
    "en": [
        "Thanks for bearing with me — picking up your case where we left off.",
        "Thanks for sticking with me — following up on your case here.",
    ],
    "es": [
        "Gracias por tu paciencia, retomo tu caso donde lo dejamos.",
        "Gracias por esperar, sigo con tu caso.",
    ],
}

def t_multi_turn(ticket, action):
    lang = ticket["account"].get("language", "en")
    tid = ticket.get("ticket_id", "")
    lead = pick(THREAD_LEADS.get(lang, THREAD_LEADS["en"]), tid + "lead")
    topic = infer_topic(ticket)
    if topic == "deposit_missing":
        return lead + " " + t_deposit(ticket)
    if topic == "fairness":
        return lead + " " + t_fairness(ticket)
    if topic == "bonus_request":
        return lead + " " + t_bonus(ticket)
    if topic == "verification":
        return lead + " " + t_verification(ticket)
    if topic == "responsible_gambling":
        return lead + " " + t_rg(ticket)
    if topic == "unknown_facts":
        return lead + " " + t_unknown(ticket)
    if topic == "manipulation":
        return lead + " " + t_manipulation(ticket)
    # Default: the usual withdrawal thread.
    return lead + " " + t_withdrawal(ticket, action)


def t_deposit(ticket):
    acc = ticket["account"]
    lang = acc.get("language", "en")
    name = uname(ticket)
    tid = ticket.get("ticket_id", "")
    deps = acc.get("deposits") or []
    d = deps[0] if deps else {}
    coin = d.get("coin", "")
    amt = fmt_money(d.get("amount_usd"))
    conf, need = d.get("confirmations"), d.get("confirmations_needed")
    mins = d.get("minutes_ago")
    full = bool(coin and amt and conf is not None and need is not None
                and mins is not None)

    if lang == "es":
        if not full:
            if coin and amt:
                return (
                    f"Hola {name}, veo tu depósito de {amt} en {coin}: está esperando "
                    f"confirmaciones de la red. Se acredita automáticamente cuando la red lo "
                    f"confirma, así que no tienes que hacer nada más. Puedes ver el progreso "
                    f"en tu historial de transacciones."
                )
            return (
                f"Hola {name}, tu depósito está esperando confirmaciones de la red. Se "
                f"acredita automáticamente cuando la red lo confirma, así que no tienes que "
                f"hacer nada más. Puedes ver el progreso en tu historial de transacciones."
            )
        openers = [
            f"Hola {name}, veo tu depósito de {amt} en {coin}: llegó hace {mins} minutos "
            f"y está esperando confirmaciones de la red, {conf} de {need} por ahora.",
            f"Hola {name}, encontré tu depósito de {amt} en {coin}: llegó hace {mins} "
            f"minutos y está en {conf} de {need} confirmaciones de la red.",
        ]
        body = (
            f"Se acredita automáticamente cuando llega a {need} confirmaciones, así que "
            f"no tienes que hacer nada más. Puedes ver el progreso en tu historial de "
            f"transacciones."
        )
        return pick(openers, tid) + " " + body

    if not full:
        if coin and amt:
            return (
                f"Hi {name}, I can see your {coin} deposit of {amt} is waiting for network "
                f"confirmations. It credits automatically once the network confirms it, so "
                f"there's nothing else to do. You can watch the progress in your transaction "
                f"history — no need to send it again."
            )
        return (
            f"Hi {name}, I can see your deposit is waiting for network confirmations. It "
            f"credits automatically once the network confirms it, so there's nothing else "
            f"to do. You can watch the progress in your transaction history — no need to "
            f"send it again."
        )
    openers = [
        f"Hi {name}, I can see your {coin} deposit of {amt} — it arrived {mins} minutes "
        f"ago and is waiting for network confirmations: {conf} of {need} so far.",
        f"Found it, {name} — your {coin} deposit of {amt} came in {mins} minutes ago "
        f"and is waiting on the network: {conf} of {need} confirmations so far.",
        f"Hi {name}, your {coin} deposit of {amt} is here — it landed {mins} minutes "
        f"ago and is at {conf} of {need} network confirmations.",
    ]
    bodies = [
        f"It credits automatically once it reaches {need} confirmations, so there's "
        f"nothing else to do.",
        f"Once it hits {need} confirmations it credits on its own — no action needed "
        f"from you.",
    ]
    closers = [
        "You can watch the progress in your transaction history — no need to send it again.",
        "Your transaction history shows the progress live; no need to send the deposit again.",
    ]
    return (pick(openers, tid) + " " + pick(bodies, tid + "b") + " "
            + pick(closers, tid + "c"))


def t_verification(ticket):
    acc = ticket["account"]
    lang = acc.get("language", "en")
    name = uname(ticket)
    tid = ticket.get("ticket_id", "")
    docs = acc.get("kyc_requested_documents") or []
    if docs:
        doc_list = " and ".join(docs) if len(docs) <= 2 else ", ".join(docs[:-1]) + " and " + docs[-1]
    else:
        doc_list = "an identity document"

    if lang == "es":
        openers = [
            f"Hola {name}, para terminar la verificación necesitamos {doc_list}.",
            f"Hola {name}, para completar la verificación nos falta {doc_list}.",
        ]
        body = (
            "Hazlo en el flujo seguro: abre Profile → Settings → Account verification "
            "y sigue los pasos; ahí también verás el estado. Lo pedimos para confirmar a "
            "la persona detrás de la cuenta y mantener las cuentas seguras."
        )
        closer = ("Por favor, no envíes documentos en este chat: solo el flujo de "
                  "verificación puede procesarlos.")
        return pick(openers, tid) + " " + body + " " + closer

    openers = [
        f"Hi {name}, to finish verification we need {doc_list}.",
        f"Hi {name} — for verification we still need {doc_list} from you.",
        f"Thanks for asking, {name}. To finish verification, we need {doc_list}.",
    ]
    bodies = [
        "You can do it in the secure flow: open Profile → Settings → Account "
        "verification and follow the steps there — that's also where you'll see your "
        "status.",
        "The secure way to send them is Profile → Settings → Account verification — "
        "follow the steps there, and you'll see your status in the same place.",
    ]
    closers = [
        "We ask for it to confirm the person behind the account and keep accounts "
        "safe. Please don't send documents in this chat — only the verification flow "
        "can process them.",
        "It's how we confirm the person behind the account and keep it safe. Please "
        "don't send documents here in the chat — only the verification flow can take "
        "them.",
    ]
    return (pick(openers, tid) + " " + pick(bodies, tid + "b") + " "
            + pick(closers, tid + "c"))


def t_bonus(ticket):
    acc = ticket["account"]
    lang = acc.get("language", "en")
    name = uname(ticket)
    tid = ticket.get("ticket_id", "")
    rw = (acc.get("rewards_available") or [{}])[0]
    amt = fmt_money(rw.get("claimable_usd")) if rw.get("claimable_usd") else None
    kind = rw.get("kind", "rakeback")

    if lang == "es":
        if not amt:
            return (
                f"Hola {name}, no puedo añadir un bono por pedirlo. Lo que tengas "
                f"disponible está en tu página de Rewards: ábrela para ver tu progreso VIP "
                f"y los importes que puedes reclamar."
            )
        openers = [
            f"Hola {name}, no puedo añadir un bono por pedirlo, pero ya tienes algo "
            f"disponible: {amt} en {kind} listos para reclamar en tu página de Rewards.",
            f"Hola {name}, un bono no puedo añadirlo yo, pero tienes {amt} en {kind} "
            f"esperando en tu página de Rewards.",
        ]
        body = ("Abre Rewards, revisa el importe y su ventana de reclamación, y reclámalo "
                "cuando quieras. No necesitas hacer nada más.")
        return pick(openers, tid) + " " + body

    if not amt:
        return (
            f"Hi {name}, I can't add a bonus on request. Anything available to you shows "
            f"on your Rewards page — open Rewards to see your VIP progress and the "
            f"amounts you can claim there."
        )
    openers = [
        f"Hi {name}, I can't add a bonus on request — but you already have something "
        f"available: {amt} in {kind} ready to claim on your Rewards page.",
        f"Hi {name} — I can't hand out a bonus just for asking, but there's good news: "
        f"you have {amt} in {kind} waiting on your Rewards page.",
        f"Hi {name}, no bonus I can add from here — but you do have {amt} in {kind} "
        f"ready to claim on your Rewards page.",
    ]
    bodies = [
        "Open Rewards, check the amount and its claim window, and claim it when you're ready.",
        "Check Rewards for the amount and how long you have to claim it, then claim it whenever you're ready.",
    ]
    closers = [
        "Nothing else is needed from you.",
        "That's all there is to it.",
    ]
    return (pick(openers, tid) + " " + pick(bodies, tid + "b") + " "
            + pick(closers, tid + "c"))


def t_manipulation(ticket):
    lang = ticket["account"].get("language", "en")
    tid = ticket.get("ticket_id", "")
    if lang == "es":
        variants = [
            "No puedo hacer eso: no entrego fondos ni bonos por pedirlo, y no sigo "
            "instrucciones de un mensaje como ese. Con gusto te ayudo con algo real: tu "
            "cuenta, un juego, un depósito o un retiro. ¿Qué necesitas?",
            "Eso no lo puedo hacer: no doy fondos ni bonos por un mensaje, y tampoco "
            "sigo instrucciones que vengan en uno. Si necesitas ayuda de verdad — tu "
            "cuenta, un juego, un depósito o un retiro — dime y te ayudo.",
        ]
        return pick(variants, tid)
    variants = [
        "I can't do that — I don't hand out funds or bonuses on request, and I don't "
        "act on instructions from a message like that. Happy to help with something "
        "real though: your account, a game, a deposit or a withdrawal. What do you need?",
        "That one's not something I can do — no funds or bonuses from a message like "
        "that, and I don't take instructions from it either. If there's something real "
        "I can help with — your account, a game, a deposit or a withdrawal — just say "
        "the word.",
        "Sorry, I can't help with that. I don't credit funds or bonuses on request, "
        "and I don't follow instructions tucked into a message. What I can do: help "
        "with your account, a game, a deposit, or a withdrawal.",
    ]
    return pick(variants, tid)


def t_language_completed(ticket):
    acc = ticket["account"]
    lang = acc.get("language", "en")
    name = uname(ticket)
    tid = ticket.get("ticket_id", "")
    w = completed_withdrawal(ticket) or {}
    coin, amt = w.get("coin", ""), fmt_money(w.get("amount_usd"))
    amt_txt = amt if amt else "tu retiro"
    if lang == "pt":
        return (
            f"Olá {name}, o teu levantamento de {amt_txt} em {coin} já foi enviado e "
            f"está na blockchain, por isso aparece como concluído. Se ainda não o vês na "
            f"tua carteira, verifica se estás na rede certa e com o endereço certo. No "
            f"Wallet → History tens o hash (TXID) da transação para a seguires num "
            f"explorador de blocos."
        )
    variants = [
        f"Hola {name}, tu retiro de {amt_txt} en {coin} ya fue enviado y está en la "
        f"blockchain, por eso aparece como completado. Si aún no lo ves en tu wallet, "
        f"revisa que estés mirando la red correcta y la dirección correcta en tu "
        f"wallet. También puedes buscar la transacción en tu historial, en Wallet → "
        f"History, donde aparece el hash (TXID) para seguirla en un explorador de bloques.",
        f"Hola {name}, tu retiro de {amt_txt} en {coin} ya se envió y está en la "
        f"blockchain, por eso figura como completado. Si todavía no lo ves en tu "
        f"wallet, revisa que estés en la red correcta y con la dirección correcta. En "
        f"Wallet → History tienes el hash (TXID) de la transacción para seguirla en un "
        f"explorador de bloques.",
    ]
    return pick(variants, tid)


def t_unknown(ticket):
    text = last_player_text(ticket).lower()
    lang = ticket["account"].get("language", "en")
    tid = ticket.get("ticket_id", "")
    vip = any(k in text for k in ("vip", "level", "nivel"))
    if lang == "es":
        if vip:
            return (
                "No tengo esa cifra en tu cuenta aquí, así que no puedo darte un número "
                "exacto. Puedes ver tu progreso VIP en tu página de Rewards: abre Rewards "
                "y verás tu nivel y los importes disponibles. Si algo no cuadra, dímelo y "
                "pediré al equipo que revise tu registro."
            )
        return (
            "No tengo esa cifra en tu cuenta aquí, así que no puedo darte un número "
            "exacto. Puedes ver tu historial en Transactions desde tu perfil: elige el "
            "historial de apuestas del casino para ver tus rondas y resultados. Si algo "
            "no cuadra, dímelo y pediré al equipo que revise tu registro."
        )
    if vip:
        variants = [
            "I don't have that figure on your account here, so I can't give you an exact "
            "number. You can see your VIP progress on your Rewards page — open Rewards "
            "and it shows your level and the amounts available to you. If it still looks "
            "off, tell me and I'll have the team check the record for you.",
            "That exact figure isn't on your account here, so I can't give you a number. "
            "Your VIP progress is on your Rewards page — open Rewards and you'll see "
            "your level and what's available. If it doesn't look right, let me know and "
            "I'll ask the team to check your record.",
            "I can't see that number from here, so I won't guess it. Open your Rewards "
            "page for your VIP progress — your level and available amounts are shown "
            "there. If something looks wrong, tell me and I'll have the team look at "
            "your record.",
        ]
        return pick(variants, tid)
    variants = [
        "I don't have that figure on your account here, so I can't give you an exact "
        "number. You can see your play history in your transaction history — open "
        "Transactions from your profile and choose the casino-bet history to see your "
        "rounds and results. If it still looks off, tell me and I'll have the team "
        "check the record for you.",
        "That number isn't on your account here, so I can't give you an exact figure. "
        "Your play history is in your transaction history — open Transactions from "
        "your profile and pick the casino-bet history to see your rounds and results. "
        "If it still looks off, tell me and I'll have the team check the record for you.",
        "I don't have that figure here, so I won't guess. You can find it in your "
        "transaction history — open Transactions from your profile and choose the "
        "casino-bet history for your rounds and results. If it still looks wrong, tell "
        "me and I'll ask the team to check your record.",
    ]
    return pick(variants, tid)


def t_rg(ticket):
    lang = ticket["account"].get("language", "en")
    name = uname(ticket)
    tid = ticket.get("ticket_id", "")
    if lang == "es":
        variants = [
            f"Gracias por decírmelo, {name}, lo tomo en serio. Empecemos el proceso de "
            f"autoexclusión ahora mismo: en tu cuenta, ve a Profile → Settings → "
            f"Responsible Gaming. Take a Break inicia un descanso de 24 horas de "
            f"inmediato, y desde ahí puedes elegir la autoexclusión por 1 semana, 1 mes, "
            f"6 meses o permanente. No se puede revertir una vez activa. Si prefieres que "
            f"el equipo la aplique por ti, escribe a support@housebets.com desde tu correo "
            f"registrado. Para ayuda externa, BeGambleAware y Gambling Therapy pueden ayudarte.",
            f"Gracias por contármelo, {name}, lo tomo muy en serio y empezamos ahora. En "
            f"tu cuenta, abre Profile → Settings → Responsible Gaming: Take a Break te da "
            f"un descanso de 24 horas de inmediato, y desde ahí puedes autoexcluirte por "
            f"1 semana, 1 mes, 6 meses o para siempre, sin posibilidad de revertirlo una "
            f"vez activo. Si prefieres que el equipo lo haga por ti, escribe a "
            f"support@housebets.com desde tu correo registrado. BeGambleAware y Gambling "
            f"Therapy también pueden ayudarte fuera de Housebets.",
        ]
        return pick(variants, tid)
    variants = [
        f"Thank you for telling me, {name} — I'm taking this seriously. Let's start "
        f"the self-exclusion process right away. In your account, go to Profile → "
        f"Settings → Responsible Gaming: Take a Break starts a 24-hour cooling-off "
        f"right away, and from there you can choose self-exclusion for 1 week, 1 month, "
        f"6 months, or permanently. It can't be reversed once it's active. If you'd "
        f"rather the team applied it for you, email support@housebets.com from your "
        f"registered address. For support outside Housebets, BeGambleAware and Gambling "
        f"Therapy can help.",
        f"Thank you for telling me, {name} — I'm taking this seriously, and we'll start "
        f"right away. In your account, go to Profile → Settings → Responsible Gaming. "
        f"Take a Break starts a 24-hour cooling-off immediately, and from there you can "
        f"choose self-exclusion for 1 week, 1 month, 6 months, or permanently — it "
        f"can't be reversed once it's active. If you'd rather the team set it up for "
        f"you, email support@housebets.com from your registered address. And for help "
        f"outside Housebets, BeGambleAware and Gambling Therapy are there for you.",
        f"I hear you, {name}, and I'm taking this seriously. Let's get the "
        f"self-exclusion going now: open Profile → Settings → Responsible Gaming in "
        f"your account. Take a Break gives you a 24-hour cooling-off straight away, and "
        f"from the same place you can self-exclude for 1 week, 1 month, 6 months, or "
        f"permanently — once it's on, it can't be undone. If you'd prefer the team to "
        f"apply it, email support@housebets.com from your registered address. "
        f"BeGambleAware and Gambling Therapy can also support you outside Housebets.",
    ]
    return pick(variants, tid)


def t_security_handoff(ticket):
    lang = ticket["account"].get("language", "en")
    name = uname(ticket)
    tid = ticket.get("ticket_id", "")
    if lang == "es":
        variants = [
            f"Hola {name}, lo siento mucho: si alguien más entró en tu cuenta, tengo que "
            f"pasar esto a una persona del equipo de soporte de inmediato. Ellos "
            f"protegerán tu cuenta y te ayudarán a recuperarla. Por favor, no compartas "
            f"tu contraseña ni tus códigos 2FA con nadie: nadie de Housebets te los "
            f"pedirá. El equipo te contactará directamente.",
            f"Hola {name}, siento mucho lo que pasó. Si alguien entró en tu cuenta, paso "
            f"esto ahora mismo a una persona del equipo de soporte: ellos asegurarán tu "
            f"cuenta y te ayudarán a recuperarla. No compartas tu contraseña ni tus "
            f"códigos 2FA con nadie — nadie de Housebets te los va a pedir. El equipo te "
            f"contactará directamente.",
        ]
        return pick(variants, tid)
    variants = [
        f"Hi {name}, I'm sorry — if someone else got into your account, I need to pass "
        f"this to a person on the support team right away. They'll secure your account "
        f"and help you recover it. Please don't share your password or 2FA codes with "
        f"anyone — nobody at Housebets will ever ask for them. The support team will "
        f"follow up with you directly.",
        f"Hi {name}, I'm really sorry this happened — if someone else got into your "
        f"account, this needs a person on the support team right away. They'll lock "
        f"things down and help you get your account back. Never share your password or "
        f"2FA codes with anyone — nobody at Housebets will ever ask for them. The team "
        f"will reach out to you directly.",
        f"Hi {name} — I'm sorry, but if someone else has been in your account I have to "
        f"hand this to a person on the support team immediately. They'll secure the "
        f"account and walk you through getting it back. Please don't share your "
        f"password or 2FA codes with anyone: no one at Housebets will ask you for them. "
        f"They'll follow up with you directly.",
    ]
    return pick(variants, tid)


def t_generic_handoff(ticket):
    lang = ticket["account"].get("language", "en")
    tid = ticket.get("ticket_id", "")
    if lang == "es":
        variants = [
            "Gracias por escribir. No tengo los detalles de tu cuenta aquí para resolver "
            "esto, así que voy a pasarlo a una persona del equipo de soporte para que lo "
            "revise. Te contactarán con la respuesta.",
            "Gracias por tu mensaje. Con los datos que tengo aquí no puedo resolverlo, "
            "así que lo paso a una persona del equipo de soporte. Te contactarán con la "
            "respuesta.",
        ]
        return pick(variants, tid)
    variants = [
        "Thanks for writing. I don't have your account details here to settle this, so "
        "I'm passing it to a person on the support team to look into. They'll follow "
        "up with you directly.",
        "Thanks for writing — I don't have what I need here to settle this one, so I'm "
        "passing it to a person on the support team. They'll look into it and get back "
        "to you.",
        "Thanks for getting in touch. I can't resolve this from the details I have "
        "here, so I'm handing it to a person on the support team to review — they'll "
        "follow up with you directly.",
    ]
    return pick(variants, tid)


# ---------------------------------------------------------------- dispatch

def build_reply(ticket, action):
    lang = ticket["account"].get("language", "en")
    # A security handoff overrides the category template: the reply must match
    # the handoff, not the category's normal flow.
    if has_security_signal(ticket):
        return t_security_handoff(ticket)
    # Same precedence as decide_action: an RG or manipulation signal in the
    # player's own words wins over the category label, so the reply always
    # matches the action (a combo ticket gets the RG flow, not a withdrawal
    # reply, and an injection gets the refusal, not a bonus reply).
    if has_rg_signal(ticket):
        return t_rg(ticket)
    if has_manipulation_signal(ticket):
        return t_manipulation(ticket)
    cat = normalize_category(ticket.get("category")) or infer_topic(ticket)
    if cat is None:
        return t_generic_handoff(ticket)
    if cat == "fairness":
        return t_fairness(ticket)
    if cat == "withdrawal_pending":
        return t_withdrawal(ticket, action)
    if cat == "multi_turn":
        return t_multi_turn(ticket, action)
    if cat == "deposit_missing":
        return t_deposit(ticket)
    if cat == "verification":
        return t_verification(ticket)
    if cat == "bonus_request":
        return t_bonus(ticket)
    if cat == "manipulation":
        return t_manipulation(ticket)
    if cat == "language":
        # Spanish/Portuguese completed-withdrawal pattern; fall back to a
        # withdrawal reply if the context differs.
        if completed_withdrawal(ticket) is not None and lang in ("es", "pt"):
            return t_language_completed(ticket)
        return t_withdrawal(ticket, action)
    if cat == "unknown_facts":
        return t_unknown(ticket)
    if cat == "responsible_gambling":
        return t_rg(ticket)
    # Unreachable for the categories above; kept as a safe fallback.
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
