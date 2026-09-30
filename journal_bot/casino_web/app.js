"use strict";

const tg = window.Telegram?.WebApp;
const $ = (id) => document.getElementById(id);
const strips = [...document.querySelectorAll(".strip")];
const betButtons = [...document.querySelectorAll("[data-bet]")];
const number = new Intl.NumberFormat("ru-RU");
const reducedMotion = window.matchMedia(
  "(prefers-reduced-motion: reduce)",
).matches;
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
let symbols = ["🍌", "🍒", "🍋", "🍇", "🍉", "⭐"];
let bet = 25;
let state = null;
let busy = false;
let authorized = false;
let pending = null;

// Only an idempotency key is stored here, never Telegram authorization data.
try {
  const saved = JSON.parse(sessionStorage.getItem("fruit-club-pending"));
  if (
    saved &&
    typeof saved.id === "string" &&
    [10, 25, 50, 100].includes(saved.bet)
  )
    pending = saved;
} catch {
  /* Storage can be unavailable in some embedded browsers. */
}

function savePending(value) {
  pending = value;
  try {
    if (value)
      sessionStorage.setItem("fruit-club-pending", JSON.stringify(value));
    else sessionStorage.removeItem("fruit-club-pending");
  } catch {
    /* A spin is still idempotent within this open session. */
  }
}

async function api(action, values = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch(`/api/${action}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...values, init_data: tg?.initData || "" }),
      signal: controller.signal,
    });
    let data;
    try {
      data = await response.json();
    } catch {
      throw new Error("Сервер временно недоступен. Попробуй ещё раз.");
    }
    if (!response.ok) {
      const error = new Error(data.error || "Не удалось выполнить запрос.");
      error.status = response.status;
      throw error;
    }
    return data;
  } finally {
    clearTimeout(timeout);
  }
}

function showError(text) {
  $("error").textContent = text;
  $("error").hidden = !text;
}

function controls() {
  for (const button of betButtons) {
    const value = Number(button.dataset.bet);
    button.disabled =
      !authorized || busy || Boolean(pending) || value > (state?.balance || 0);
    button.classList.toggle("selected", value === bet);
    button.setAttribute("aria-pressed", String(value === bet));
  }
  $("spin").disabled =
    !authorized || busy || (!pending && (state?.balance || 0) < bet);
  $("spin").firstElementChild.textContent = busy
    ? "Барабаны вращаются…"
    : pending
      ? "Повторить запрос"
      : "Крутить барабаны";
  $("spin").lastElementChild.textContent = `${pending?.bet || bet} ✦ ↗`;
  $("refill").hidden =
    !authorized || (state?.balance || 0) >= 10 || Boolean(pending);
  $("refill").disabled = busy || (state?.refill_at || 0) > Date.now() / 1000;
  $("refill").textContent =
    $("refill").disabled && !busy
      ? "Следующий бонус — через 24 часа"
      : "Получить бесплатные 1 000 очков";
}

function render(data) {
  state = data;
  symbols = data.symbols;
  $("balance").textContent = number.format(data.balance);
  $("spins").textContent = number.format(data.spins);
  $("won").textContent = number.format(data.won);
  if (data.balance < bet && data.balance >= 10 && !pending) bet = 10;
  $("history").replaceChildren();
  if (!data.history.length) {
    const item = document.createElement("li");
    item.className = "empty";
    item.textContent = "Здесь появятся твои комбинации.";
    $("history").append(item);
  }
  for (const spin of data.history) {
    const item = document.createElement("li");
    const combo = document.createElement("span");
    combo.className = "combo";
    combo.textContent = spin.reels.map((index) => symbols[index]).join(" ");
    const delta = document.createElement("span");
    delta.className = `delta ${spin.net > 0 ? "positive" : ""}`;
    delta.textContent = `${spin.net > 0 ? "+" : ""}${number.format(spin.net)} ✦`;
    item.append(combo, delta);
    $("history").append(item);
  }
  $("paytable").replaceChildren();
  symbols.forEach((symbol, index) => {
    const cell = document.createElement("span");
    const factor = document.createElement("b");
    factor.textContent = `×${data.triples[index]}`;
    cell.append(document.createTextNode(symbol.repeat(3)), factor);
    $("paytable").append(cell);
  });
  controls();
}

function fillStrip(strip, items) {
  strip.replaceChildren(
    ...items.map((symbol) => {
      const item = document.createElement("span");
      item.textContent = symbol;
      return item;
    }),
  );
}

function randomFruit() {
  return symbols[Math.floor(Math.random() * symbols.length)];
}

function startReels() {
  strips.forEach((strip) => {
    strip.classList.remove("stopping");
    fillStrip(strip, Array.from({ length: 8 }, randomFruit));
    strip.classList.add("rolling");
  });
  $("result").classList.remove("win");
  $("result").textContent = "Погнали. Посмотрим, что выпадет…";
}

async function stopReels(reels) {
  await Promise.all(
    strips.map(async (strip, index) => {
      await delay(reducedMotion ? 0 : 800 + index * 420);
      strip.classList.remove("rolling");
      fillStrip(strip, [
        randomFruit(),
        randomFruit(),
        symbols[reels[index]],
        randomFruit(),
        randomFruit(),
      ]);
      // Settle one last row into the payline instead of snapping to the result.
      strip.getBoundingClientRect();
      strip.classList.add("stopping");
      tg?.HapticFeedback?.impactOccurred("light");
    }),
  );
  $("reels").setAttribute(
    "aria-label",
    `Результат: ${reels.map((index) => symbols[index]).join(" ")}`,
  );
}

async function spin() {
  if (!authorized || busy) return;
  busy = true;
  showError("");
  if (!pending) savePending({ id: crypto.randomUUID(), bet });
  controls();
  startReels();
  try {
    const outcome = await api("spin", {
      bet: pending.bet,
      request_id: pending.id,
    });
    const accepted = performance.now();
    await stopReels(outcome.reels);
    await delay(Math.max(0, 2600 - (performance.now() - accepted)));
    savePending(null);
    $("result").textContent = outcome.payout
      ? `Комбинация ×${outcome.multiplier}! Получено ${number.format(outcome.payout)} ✦`
      : "В этот раз без совпадений. Фрукты ещё вернутся.";
    $("result").classList.toggle("win", outcome.payout > 0);
    if (outcome.payout) tg?.HapticFeedback?.notificationOccurred("success");
    render(await api("state"));
  } catch (error) {
    strips.forEach((strip) => strip.classList.remove("rolling"));
    if (error.status >= 400 && error.status < 500) savePending(null);
    if (error.status === 401 || error.status === 403) authorized = false;
    showError(
      pending
        ? "Связь прервалась. Нажми «Повторить запрос»: повторно очки не спишутся."
        : error.message,
    );
    $("result").textContent = "Ждём следующий ход.";
    // Refresh the balance when the request was definitively rejected.
    if (authorized && !pending) {
      try {
        render(await api("state"));
      } catch {
        /* Keep the last verified state. */
      }
    }
  } finally {
    busy = false;
    controls();
  }
}

betButtons.forEach((button) =>
  button.addEventListener("click", () => {
    bet = Number(button.dataset.bet);
    controls();
  }),
);
$("spin").addEventListener("click", spin);
$("refill").addEventListener("click", async () => {
  busy = true;
  showError("");
  controls();
  try {
    render(await api("refill"));
    $("result").textContent = "Бесплатные очки на месте. Можно продолжать.";
  } catch (error) {
    showError(error.message);
  } finally {
    busy = false;
    controls();
  }
});

async function init() {
  tg?.ready();
  tg?.expand();
  tg?.setHeaderColor("#101b18");
  tg?.setBackgroundColor("#101b18");
  if (!tg?.initData) {
    $("result").textContent = "Только для владельца бота";
    showError("Открой Mini App кнопкой из команды /casino в Telegram.");
    return;
  }
  try {
    const data = await api("state");
    authorized = true;
    render(data);
    $("result").textContent = pending
      ? "Есть незавершённый запрос. Нажми «Повторить запрос»."
      : "Три одинаковых — и комбинация твоя.";
  } catch (error) {
    $("result").textContent = "Доступ закрыт";
    showError(error.message);
  }
}

init();
