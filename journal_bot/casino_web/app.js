"use strict";

const tg = window.Telegram?.WebApp;
const $ = (id) => document.getElementById(id);
const strips = [...document.querySelectorAll(".strip")];
const betButtons = [...document.querySelectorAll("[data-bet]")];
const number = new Intl.NumberFormat("ru-RU");
const dateTime = new Intl.DateTimeFormat("ru-RU", {
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  timeZone: "Asia/Yekaterinburg",
});
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
let activeView = "menu";
let historyItems = [];
let historyOffset = null;
let historyLoading = false;
let historyReload = false;
let boardRequest = 0;

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
  document.querySelectorAll("[data-route]").forEach((button) => {
    button.disabled = !authorized;
  });
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
      : "Получить бонус: 1 000 монет";
}

function render(data) {
  state = data;
  symbols = data.symbols;
  document.querySelectorAll("[data-balance]").forEach((element) => {
    element.textContent = number.format(data.balance);
  });
  $("spins").textContent = number.format(data.spins);
  $("rank").textContent = `#${data.rank}`;
  $("player-name").textContent = data.profile.name;
  $("player-username").textContent = data.profile.username
    ? `@${data.profile.username}`
    : "Профиль Telegram";
  renderAvatar($("avatar"), data.profile);
  renderHistory($("recent-history"), data.history.slice(0, 3));
  renderModes(data.modes);
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

function renderAvatar(container, profile) {
  container.replaceChildren(
    document.createTextNode((profile.name || "?").slice(0, 1).toUpperCase()),
  );
  if (profile.photo_url) {
    const image = document.createElement("img");
    image.src = profile.photo_url;
    image.alt = profile.name;
    image.referrerPolicy = "no-referrer";
    image.addEventListener("error", () => image.remove());
    container.append(image);
  }
}

function renderHistory(container, games) {
  container.replaceChildren();
  if (!games.length) {
    const empty = document.createElement("li");
    empty.className = "empty";
    empty.textContent = "Твоя история начнётся с первой игры.";
    container.append(empty);
  }
  for (const game of games) {
    const item = document.createElement("li");
    const icon = document.createElement("span");
    icon.className = "history-icon";
    icon.textContent = game.mode === "fruit_slots" ? "🍌" : "✦";
    const info = document.createElement("div");
    info.className = "history-info";
    const title = document.createElement("strong");
    title.textContent =
      state.modes.find((mode) => mode.id === game.mode)?.name || "Мини-игра";
    const time = document.createElement("time");
    const date = new Date(Number(game.created) * 1000);
    time.textContent = Number.isFinite(date.getTime())
      ? dateTime.format(date)
      : "Время не указано";
    if (Number.isFinite(date.getTime())) time.dateTime = date.toISOString();
    info.append(title, time);
    const delta = document.createElement("span");
    delta.className = `delta ${game.net >= 0 ? "positive" : "negative"}`;
    delta.textContent = `${game.net >= 0 ? "+" : "−"}${number.format(Math.abs(game.net))} ✦`;
    item.append(icon, info, delta);
    container.append(item);
  }
}

function renderModes(modes) {
  $("mode-list").replaceChildren();
  for (const mode of modes) {
    const card = $("mode-card").content.firstElementChild.cloneNode(true);
    card.querySelector("h2").textContent = mode.name;
    card.querySelector("p").textContent = mode.description;
    const button = card.querySelector("button");
    button.disabled = !mode.available || !authorized;
    button.addEventListener("click", () => {
      if (mode.id === "fruit_slots") navigate("game");
    });
    $("mode-list").append(card);
  }
}

function trophy(rank) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute(
    "aria-label",
    ["Золотой кубок", "Серебряный кубок", "Бронзовый кубок"][rank - 1],
  );
  svg.setAttribute("role", "img");
  svg.classList.add("cup", ["gold", "silver", "bronze"][rank - 1]);
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute(
    "d",
    "M7 2h10v2h4v4c0 3-2 5-5 5a6 6 0 0 1-3 2v4h4v3H7v-3h4v-4a6 6 0 0 1-3-2C5 13 3 11 3 8V4h4V2Zm0 4H5v2c0 1.5.7 2.5 2 3V6Zm10 0v5c1.3-.5 2-1.5 2-3V6h-2Z",
  );
  path.setAttribute("fill", "currentColor");
  svg.append(path);
  return svg;
}

async function loadLeaderboard() {
  const request = ++boardRequest;
  try {
    const data = await api("leaderboard");
    if (request !== boardRequest) return;
    $("player-count").textContent = `Игроков: ${number.format(data.total)}`;
    $("board-rank").textContent = `#${data.me.rank}`;
    $("board-balance").textContent = `${number.format(data.me.balance)} ✦`;
    $("leaderboard").replaceChildren();
    for (const player of data.players) {
      const item = document.createElement("li");
      if (player.user_id === data.me.user_id) item.classList.add("is-me");
      const place = document.createElement("span");
      place.className = "place";
      if (player.rank <= 3) place.append(trophy(player.rank));
      else place.textContent = player.rank;
      const avatar = document.createElement("span");
      avatar.className = "avatar small";
      renderAvatar(avatar, player);
      const name = document.createElement("span");
      name.className = "leader-name";
      name.textContent = player.name;
      const balance = document.createElement("strong");
      balance.textContent = `${number.format(player.balance)} ✦`;
      item.append(place, avatar, name, balance);
      $("leaderboard").append(item);
    }
  } catch (error) {
    if (request === boardRequest && activeView === "leaderboard")
      showError(error.message);
  }
}

async function loadHistory(reset = false) {
  if (historyLoading) {
    if (reset) historyReload = true;
    return;
  }
  historyLoading = true;
  $("history-more").disabled = true;
  try {
    const data = await api("history", { offset: reset ? 0 : historyOffset });
    historyItems = reset ? data.items : [...historyItems, ...data.items];
    historyOffset = data.next_offset;
    renderHistory($("all-history"), historyItems);
    $("history-more").hidden = historyOffset === null;
  } catch (error) {
    showError(error.message);
  } finally {
    historyLoading = false;
    $("history-more").disabled = false;
    if (historyReload) {
      historyReload = false;
      loadHistory(true);
    }
  }
}

function navigate(view) {
  if (
    !authorized ||
    !["menu", "modes", "leaderboard", "history", "game"].includes(view)
  )
    return;
  activeView = view;
  document.querySelectorAll(".view").forEach((section) => {
    section.hidden = section.id !== `view-${view}`;
  });
  document.querySelectorAll(".navigation [data-route]").forEach((button) => {
    if (button.dataset.route === (view === "game" ? "modes" : view))
      button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  showError("");
  window.scrollTo({ top: 0, behavior: "instant" });
  if (view === "leaderboard") loadLeaderboard();
  if (view === "history") loadHistory(true);
  if (view === "game") {
    tg?.BackButton?.show();
    if (!busy)
      $("result").textContent = pending
        ? "Есть незавершённый ход. Нажми «Повторить запрос»."
        : "Три одинаковых — и комбинация твоя.";
  } else tg?.BackButton?.hide();
}

async function refreshState() {
  render(await api("state"));
  if (activeView === "leaderboard") loadLeaderboard();
  if (activeView === "history") loadHistory(true);
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
    await refreshState();
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
        await refreshState();
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
    $("result").textContent = "Бонус на месте. Можно продолжать.";
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
    $("player-name").textContent = "Доступ закрыт";
    showError("Открой мини-игры кнопкой из команды /play в Telegram.");
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
    $("player-name").textContent = "Доступ закрыт";
    showError(error.message);
  }
}

document
  .querySelectorAll("[data-route]")
  .forEach((button) =>
    button.addEventListener("click", () => navigate(button.dataset.route)),
  );
$("history-more").addEventListener("click", () => loadHistory());
tg?.BackButton?.onClick(() => navigate("modes"));

init();
