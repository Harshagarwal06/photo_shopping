const state = {
  draft: null,
  dryRun: true,
  demoMode: false,
  autoAdd: false,
  checkoutDisabled: true,
  provider: "blinkit",
  providerName: "grocery provider",
  providerConnected: false,
  providerReady: false,
  providerStatusMessage: "",
  cartMutationsAllowed: false,
  availableProviders: [],
  lastTotal: 0,
  comparisonProposal: null,
  comparisonOperation: null,
  contract: null,
  plan: null,
  pendingAction: "draft",
  cloudRetryAvailable: false,
  cloudRetryProvider: "cloud",
  recognitionPolicy: "review",
  photoQuality: null,
  photoQualityController: null,
};

const ui = {
  form: document.querySelector("#request-form"),
  text: document.querySelector("#request-text"),
  image: document.querySelector("#request-image"),
  uploadBox: document.querySelector("#upload-box"),
  fileName: document.querySelector("#file-name"),
  photoQuality: document.querySelector("#photo-quality"),
  help: document.querySelector("#request-help"),
  error: document.querySelector("#request-error"),
  draftButton: document.querySelector("#draft-button"),
  compareButton: document.querySelector("#compare-button"),
  comparisonProviders: document.querySelector("#comparison-providers"),
  loginButton: document.querySelector("#login-button"),
  providerSelect: document.querySelector("#provider-select"),
  addressPicker: document.querySelector("#address-picker"),
  addressSelect: document.querySelector("#address-select"),
  addressEmpty: document.querySelector("#address-empty"),
  refreshAddresses: document.querySelector("#refresh-addresses"),
  modeBadge: document.querySelector("#mode-badge"),
  dishCapability: document.querySelector("#dish-capability"),
  shopPathTitle: document.querySelector("#shop-path-title"),
  shopPathHelp: document.querySelector("#shop-path-help"),
  shopModeNote: document.querySelector("#shop-mode-note"),
  progress: document.querySelector("#progress"),
  progressMessage: document.querySelector("#progress-message"),
  stages: [...document.querySelectorAll("#stage-list li")],
  transcription: document.querySelector("#transcription"),
  transcriptionItems: document.querySelector("#transcription-items"),
  transcriptionNotice: document.querySelector("#transcription-notice"),
  transcriptionSummary: document.querySelector("#transcription-summary"),
  addTranscriptionItem: document.querySelector("#add-transcription-item"),
  cloudRetry: document.querySelector("#cloud-retry"),
  continueReviewed: document.querySelector("#continue-reviewed"),
  contract: document.querySelector("#cartproof-contract"),
  contractItems: document.querySelector("#cartproof-contract-items"),
  contractBudget: document.querySelector("#cartproof-cart-budget"),
  contractNote: document.querySelector("#cartproof-contract-note"),
  confirmContract: document.querySelector("#confirm-cartproof-contract"),
  review: document.querySelector("#review"),
  reviewTitle: document.querySelector("#review-title"),
  reviewSummary: document.querySelector("#review-summary"),
  groups: document.querySelector("#review-groups"),
  cartFlags: document.querySelector("#cart-flags"),
  comparison: document.querySelector("#comparison"),
  comparisonSummary: document.querySelector("#comparison-summary"),
  comparisonEditHelp: document.querySelector("#comparison-edit-help"),
  comparisonReasonDetails: document.querySelector("#comparison-reason-details"),
  comparisonWinner: document.querySelector("#comparison-winner"),
  comparisonReasons: document.querySelector("#comparison-reasons"),
  comparisonGrid: document.querySelector("#comparison-grid"),
  verifyComparison: document.querySelector("#verify-comparison"),
  comparisonDecision: document.querySelector("#comparison-decision"),
  total: document.querySelector("#draft-total"),
  totalBlock: document.querySelector(".total-block"),
  budgetStatus: document.querySelector("#budget-status"),
  confirmBar: document.querySelector("#confirm-bar"),
  confirmButton: document.querySelector("#confirm-button"),
  confirmCount: document.querySelector("#confirm-count"),
  confirmMode: document.querySelector("#confirm-mode"),
  dialog: document.querySelector("#summary-dialog"),
  summaryCopy: document.querySelector("#summary-copy"),
  summaryList: document.querySelector("#summary-list"),
  closeSummary: document.querySelector("#close-summary"),
  summaryDone: document.querySelector("#summary-done"),
  toastStack: document.querySelector("#toast-stack"),
};

const money = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 2,
});

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function setButtonState(button, status = "default") {
  if (status === "default") delete button.dataset.state;
  else button.dataset.state = status;
  button.disabled = status === "loading";
  button.setAttribute("aria-busy", status === "loading" ? "true" : "false");
}

function showRequestError(message) {
  ui.error.textContent = message;
  ui.error.hidden = !message;
  ui.help.hidden = Boolean(message);
  ui.text.setAttribute("aria-invalid", message ? "true" : "false");
  ui.uploadBox.classList.toggle("is-error", Boolean(message));
}

function setStage(name) {
  const order = ["planner", "retrieval", "matcher", "constraints"];
  const current = order.indexOf(name);
  ui.stages.forEach((element) => {
    const index = order.indexOf(element.dataset.stage);
    element.classList.toggle("is-active", index === current);
    element.classList.toggle("is-done", current >= 0 && index < current);
    if (index === current) element.setAttribute("aria-current", "step");
    else element.removeAttribute("aria-current");
  });
}

function completeStages() {
  ui.stages.forEach((element) => {
    element.classList.remove("is-active");
    element.classList.add("is-done");
    element.removeAttribute("aria-current");
  });
}

function updateActionCopy() {
  const providerName = state.providerName || "your shopping app";
  ui.shopPathTitle.textContent = `Shop on ${providerName}`;
  ui.shopModeNote.classList.remove("is-live", "is-safe");

  if (state.demoMode) {
    ui.shopPathHelp.textContent = "Try the full matching flow with sample provider data.";
    ui.shopModeNote.textContent = "Demo mode: no shopping app or cart can be changed.";
    ui.shopModeNote.classList.add("is-safe");
    ui.draftButton.querySelector(".button-label").textContent = "Preview product matches";
    return;
  }

  if (state.autoAdd) {
    ui.shopPathHelp.textContent = `Search ${providerName} and use the closest in-stock product for each item.`;
    ui.shopModeNote.textContent = state.providerReady
      ? `Auto-add is on: matches will be added to ${providerName}. Checkout and payment stay off.`
      : state.providerStatusMessage || `Connect ${providerName} above before shopping. Auto-add will be on after connection.`;
    ui.shopModeNote.classList.add(state.providerReady ? "is-live" : "is-safe");
    ui.draftButton.querySelector(".button-label").textContent = "Search and add";
    return;
  }

  if (!state.cartMutationsAllowed) {
    ui.shopPathHelp.textContent = `Preview the products ${providerName} would use for this list.`;
    ui.shopModeNote.textContent = `Preview mode: this cannot change your ${providerName} cart.`;
    ui.shopModeNote.classList.add("is-safe");
    ui.draftButton.querySelector(".button-label").textContent = "Preview matches";
    return;
  }

  ui.shopPathHelp.textContent = `Find products on ${providerName}, then review them before anything is added.`;
  ui.shopModeNote.textContent = state.providerReady
    ? `Review mode: you approve the selected products and pack counts before adding them.`
    : state.providerStatusMessage || `Connect ${providerName} above before shopping.`;
  ui.shopModeNote.classList.add(state.providerReady ? "is-live" : "is-safe");
  ui.draftButton.querySelector(".button-label").textContent = "Find matches";
}

function showToast(message, { tone = "default", action = null, sticky = false } = {}) {
  const toast = document.createElement("div");
  toast.className = `toast${tone === "error" ? " is-error" : ""}`;
  const copy = document.createElement("span");
  copy.textContent = message;
  toast.append(copy);
  if (action) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "text-button";
    button.textContent = action.label;
    button.addEventListener("click", () => {
      action.run();
      toast.remove();
    });
    toast.append(button);
  }
  ui.toastStack.append(toast);
  if (!sticky) window.setTimeout(() => toast.remove(), 7000);
}

function selectedProduct(item) {
  return item.candidates.find((product) => product.id === item.selected_product_id) ?? null;
}

function activeItems() {
  return state.draft?.items.filter(
    (item) => !item.removed && item.selected_product_id && item.units_to_add > 0,
  ) ?? [];
}

function currentTotal() {
  return activeItems().reduce((sum, item) => {
    const product = selectedProduct(item);
    return sum + (product ? product.price * item.units_to_add : 0);
  }, 0);
}

function animateTotal(target) {
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const start = state.lastTotal;
  state.lastTotal = target;
  if (reduced || start === target) {
    ui.total.textContent = money.format(target);
    return;
  }
  const started = performance.now();
  const duration = 400;
  const tick = (now) => {
    const progress = Math.min(1, (now - started) / duration);
    const eased = 1 - Math.pow(1 - progress, 3);
    ui.total.textContent = money.format(start + (target - start) * eased);
    if (progress < 1) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

function updateSummary() {
  if (!state.draft) return;
  const total = currentTotal();
  animateTotal(total);
  const budget = state.draft.cart_budget;
  const over = budget != null && total > budget;
  ui.totalBlock.classList.toggle("is-over", over);
  ui.budgetStatus.textContent = budget == null
    ? "No cart budget set"
    : over
      ? `${money.format(total - budget)} over ${money.format(budget)}`
      : `${money.format(budget - total)} left of ${money.format(budget)}`;
  const items = activeItems();
  ui.confirmCount.textContent = `${items.length} item${items.length === 1 ? "" : "s"} selected`;
  ui.confirmButton.disabled = items.length === 0;
}

function mediaMarkup(product) {
  if (product.image_url) {
    return `<img src="${escapeHtml(product.image_url)}" alt="${escapeHtml(product.name)}" width="480" height="360" loading="lazy" referrerpolicy="no-referrer" />`;
  }
  return '<span class="parcel-mark" aria-hidden="true">PACK</span>';
}

function candidateMarkup(item, product) {
  const disabled = !product.in_stock;
  const facts = [];
  if (product.past_order_count) {
    facts.push(`Ordered ${product.past_order_count}× before`);
  }
  if (product.rating != null) {
    facts.push(`${product.rating}★${product.review_count ? ` · ${product.review_count.toLocaleString("en-IN")} reviews` : ""}`);
  } else {
    facts.push(`${state.providerName} reviews not shown`);
  }
  if (product.discount_percent) facts.push(`${product.discount_percent}% off`);
  if (product.delivery_minutes != null) facts.push(`${product.delivery_minutes} min delivery`);
  const mrp = product.mrp && product.mrp > product.price
    ? `<s class="candidate-mrp">${money.format(product.mrp)}</s>`
    : "";
  return `
    <div class="candidate is-selected${disabled ? " is-out" : ""}" data-candidate-id="${escapeHtml(product.id)}">
      <span class="candidate-media">${mediaMarkup(product)}</span>
      <span class="candidate-copy">
        <span class="candidate-name">${escapeHtml(product.name)}</span>
        <span class="candidate-pack">${escapeHtml(product.pack_size || "Pack size unavailable")}${disabled ? " · Out of stock" : ""}</span>
        <span class="candidate-facts">${facts.map(escapeHtml).join(" · ")}</span>
        <span class="candidate-price">${money.format(product.price)} ${mrp}</span>
      </span>
      <span class="pick-mark" aria-hidden="true">BEST</span>
    </div>`;
}

function itemMarkup(item) {
  const selected = selectedProduct(item);
  const providerQuery = item.planned.provider_query || item.planned.search_term;
  const candidates = selected
    ? `<div class="candidate-grid is-single">${candidateMarkup(item, selected)}</div>`
    : `<div class="empty-results"><h3>No ${escapeHtml(state.providerName)} results</h3><p>Change the search phrase above and try again.</p></div>`;
  const flags = item.flags?.length
    ? `<div class="item-flags" role="status">${item.flags.map((flag) => `<span>Review: ${escapeHtml(flag)}</span>`).join("")}</div>`
    : "";
  const tools = state.autoAdd
    ? '<span class="selection-status">Selected and added automatically</span>'
    : `<form class="query-editor" data-action="research">
        <label for="query-${escapeHtml(item.planned.id)}">Search query for ${escapeHtml(providerQuery)}</label>
        <input class="query-input" id="query-${escapeHtml(item.planned.id)}" value="${escapeHtml(providerQuery)}" />
        <button class="text-button" type="submit">Search</button>
      </form>
      <button class="text-button" type="button" data-action="remove">${item.removed ? "Restore" : "Remove"}</button>`;
  const quantity = state.autoAdd
    ? `<p class="auto-quantity">${escapeHtml(item.units_to_add)} pack${item.units_to_add === 1 ? "" : "s"} added to ${escapeHtml(state.providerName)}</p>`
    : `<div class="quantity-control">
        <span>Packs to add</span>
        <button class="qty-button" type="button" data-action="decrement" aria-label="Decrease packs">−</button>
        <input class="qty-input" type="number" min="1" max="50" inputmode="numeric" value="${escapeHtml(Math.max(1, item.units_to_add))}" aria-label="Packs to add for ${escapeHtml(item.planned.search_term)}" />
        <button class="qty-button" type="button" data-action="increment" aria-label="Increase packs">+</button>
      </div>`;
  return `
    <article class="draft-item${item.removed ? " is-removed" : ""}" data-item-id="${escapeHtml(item.planned.id)}">
      <header class="item-head">
        <div class="item-title">
          <h3>${escapeHtml(item.planned.search_term)}</h3>
          <span class="raw-text">From “${escapeHtml(item.planned.raw_text || item.planned.search_term)}” · ${escapeHtml(item.planned.quantity)} ${escapeHtml(item.planned.unit)}</span>
        </div>
        <div class="item-tools">
          ${tools}
        </div>
      </header>
      ${candidates}
      <footer class="item-foot">
        <div>
          <p class="match-reason">${escapeHtml(item.reason || "Choose the closest product match.")}</p>
          ${flags}
        </div>
        ${quantity}
      </footer>
    </article>`;
}

function renderDraft() {
  if (!state.draft) return;
  const grouped = new Map();
  state.draft.items.forEach((item) => {
    const expanded = item.planned.source.startsWith("expanded from:");
    const key = expanded ? item.planned.source.replace("expanded from:", "").trim() : "Requested items";
    if (!grouped.has(key)) grouped.set(key, []);
    grouped.get(key).push(item);
  });
  ui.groups.innerHTML = [...grouped.entries()].map(([name, items]) => {
    const isDish = name !== "Requested items";
    return `<section class="dish-group" aria-labelledby="group-${escapeHtml(name.replaceAll(" ", "-").toLowerCase())}">
      <div class="group-title">
        <h3 id="group-${escapeHtml(name.replaceAll(" ", "-").toLowerCase())}">${isDish ? `Ingredients for ${escapeHtml(name)}` : escapeHtml(name)}</h3>
        <span>${items.length} planned item${items.length === 1 ? "" : "s"}</span>
      </div>
      ${items.map(itemMarkup).join("")}
    </section>`;
  }).join("");

  const notices = (state.draft.notices ?? [])
    .map((notice) => `<div class="flag is-info"><span aria-hidden="true">i</span><span>${escapeHtml(notice)}</span></div>`);
  const addMessages = (state.draft.auto_add_messages ?? [])
    .map((message) => `<div class="flag is-info"><span aria-hidden="true">✓</span><span>${escapeHtml(message)}</span></div>`);
  const addErrors = (state.draft.auto_add_errors ?? [])
    .map((message) => `<div class="flag"><span aria-hidden="true">!</span><span>${escapeHtml(message)}</span></div>`);
  const warnings = (state.draft.flags ?? [])
    .map((flag) => `<div class="flag"><span aria-hidden="true">!</span><span>${escapeHtml(flag)}</span></div>`);
  ui.cartFlags.innerHTML = [...notices, ...addMessages, ...addErrors, ...warnings].join("");
  ui.totalBlock.querySelector(":scope > span").textContent = state.autoAdd
    ? "Products total"
    : state.cartMutationsAllowed ? "Selected total" : "Preview total";
  if (state.autoAdd) {
    const failed = (state.draft.auto_add_errors ?? []).length;
    ui.reviewTitle.textContent = failed
      ? "Some products could not be added"
      : `${state.providerName} cart updated`;
    ui.reviewSummary.textContent = failed
      ? "Review the messages and selected products below. Checkout and payment were not opened."
      : "Review what was added below. Checkout and payment were not opened.";
  } else if (!state.cartMutationsAllowed) {
    ui.reviewTitle.textContent = `Preview the ${state.providerName} matches`;
    ui.reviewSummary.textContent = "Check each product and pack count. Preview mode cannot change the cart.";
  } else {
    ui.reviewTitle.textContent = `Review your ${state.providerName} matches`;
    ui.reviewSummary.textContent = "Check each product and pack count, then add the selected items from the bar below.";
  }
  updateSummary();
}

function selectedComparisonProviders() {
  return [...ui.comparisonProviders.querySelectorAll('input[type="checkbox"]:checked')]
    .map((input) => input.value);
}

const transcriptionUnits = ["item", "count", "g", "kg", "ml", "l", "pack"];

function transcriptionRowMarkup(item) {
  const warning = item.needs_review;
  const notes = (item.recognition_notes ?? []).join(" ");
  const alternatives = (item.alternatives ?? []).slice(0, 4);
  return `
    <article class="transcription-row${warning ? " is-review" : ""}" data-plan-item="${escapeHtml(item.id)}">
      <label class="transcription-include">
        <input type="checkbox" data-field="include" ${item.confirmed ? "checked" : ""} />
        <span>Include</span>
      </label>
      <div class="transcription-field">
        <label for="plan-name-${escapeHtml(item.id)}">Product</label>
        <input id="plan-name-${escapeHtml(item.id)}" data-field="search_term" value="${escapeHtml(item.search_term)}" />
      </div>
      <div class="transcription-field">
        <label for="plan-context-${escapeHtml(item.id)}">Brand or note</label>
        <input id="plan-context-${escapeHtml(item.id)}" data-field="context" value="${escapeHtml(item.context)}" />
      </div>
      <div class="transcription-field">
        <label for="plan-quantity-${escapeHtml(item.id)}">Quantity</label>
        <input id="plan-quantity-${escapeHtml(item.id)}" data-field="quantity" type="number" min="0.01" step="any" value="${escapeHtml(item.quantity)}" />
      </div>
      <div class="transcription-field">
        <label for="plan-unit-${escapeHtml(item.id)}">Unit</label>
        <select id="plan-unit-${escapeHtml(item.id)}" data-field="unit">
          ${transcriptionUnits.map((unit) => `<option value="${unit}" ${unit === item.unit ? "selected" : ""}>${unit}</option>`).join("")}
        </select>
      </div>
      ${item.crop_box?.length === 4 ? `<canvas class="transcription-crop" data-crop="${item.crop_box.map(Number).join(",")}" aria-label="Handwriting crop for ${escapeHtml(item.search_term)}"></canvas>` : ""}
      <p class="transcription-source">Read from “${escapeHtml(item.raw_text || item.search_term)}” · reading confidence ${Math.round((item.confidence ?? 0) * 100)}%</p>
      ${alternatives.length ? `<p class="transcription-alternatives">Other readings: ${alternatives.map((alternative) => `<span>“${escapeHtml(alternative)}”</span>`).join(" · ")}</p>` : ""}
      ${warning ? `<p class="transcription-warning">${escapeHtml(notes || "This line needs confirmation before search.")}</p>` : ""}
    </article>`;
}

async function renderTranscriptionCrops() {
  const file = ui.image.files[0];
  if (!file || !window.createImageBitmap) return;
  const bitmap = await createImageBitmap(file);
  for (const canvas of ui.transcriptionItems.querySelectorAll("[data-crop]")) {
    const [x, y, width, height] = canvas.dataset.crop.split(",").map(Number);
    const xPad = Math.max(0.02, width * 0.06);
    const yPad = Math.max(0.012, height * 0.3);
    const sx = Math.max(0, (x - xPad) * bitmap.width);
    const sy = Math.max(0, (1 - (y + height / 2 + yPad)) * bitmap.height);
    const sw = Math.min(bitmap.width - sx, (width + xPad * 2) * bitmap.width);
    const sh = Math.min(bitmap.height - sy, (height + yPad * 2) * bitmap.height);
    canvas.width = 720;
    canvas.height = Math.max(80, Math.round(720 * sh / Math.max(1, sw)));
    canvas.getContext("2d").drawImage(
      bitmap, sx, sy, sw, sh, 0, 0, canvas.width, canvas.height,
    );
  }
  bitmap.close();
}

function renderTranscription() {
  if (!state.plan) return;
  const uncertain = state.plan.items.filter((item) => item.needs_review).length;
  ui.transcriptionItems.innerHTML = state.plan.items.map(transcriptionRowMarkup).join("");
  renderTranscriptionCrops().catch(() => {
    // The editable transcription remains usable if a browser cannot render crops.
  });
  ui.transcriptionNotice.textContent = state.plan.processing_note || "";
  ui.transcriptionSummary.textContent = uncertain
    ? `${state.plan.items.length} lines found; ${uncertain} need confirmation. Unchecked lines will not be searched.`
    : `${state.plan.items.length} lines found. Check the transcription before provider search.`;
  ui.cloudRetry.hidden = !(
    uncertain
    && state.cloudRetryAvailable
    && ui.image.files[0]
  );
  ui.continueReviewed.querySelector(".button-label").textContent =
    state.pendingAction === "compare" ? "Compare these items" : `Search ${state.providerName} for these items`;
  ui.transcription.hidden = false;
  ui.progress.hidden = true;
  ui.transcription.scrollIntoView({ behavior: "smooth", block: "start" });
}

function syncTranscriptionEdits() {
  if (!state.plan) return;
  for (const row of ui.transcriptionItems.querySelectorAll("[data-plan-item]")) {
    const item = state.plan.items.find(
      (candidate) => candidate.id === row.dataset.planItem,
    );
    if (!item) continue;
    const included = row.querySelector('[data-field="include"]').checked;
    item.search_term = row.querySelector('[data-field="search_term"]').value;
    item.context = row.querySelector('[data-field="context"]').value;
    item.quantity = row.querySelector('[data-field="quantity"]').value;
    item.unit = row.querySelector('[data-field="unit"]').value;
    item.confirmed = included;
    if (included) item.needs_review = false;
  }
}

function addMissingTranscriptionItem() {
  if (!state.plan) return;
  syncTranscriptionEdits();
  const id = `manual-${globalThis.crypto?.randomUUID?.() ?? Date.now()}`;
  state.plan.items.push({
    id,
    search_term: "",
    context: "",
    quantity: 1,
    unit: "item",
    raw_text: "Added manually",
    source: "text",
    confidence: 1,
    needs_review: false,
    confirmed: true,
    alternatives: [],
    crop_box: [],
    recognition_notes: [],
  });
  renderTranscription();
  const row = ui.transcriptionItems.querySelector(
    `[data-plan-item="${CSS.escape(id)}"]`,
  );
  row?.querySelector('[data-field="search_term"]')?.focus();
}

function reviewedPlanFromForm() {
  if (!state.plan) return null;
  const items = [];
  for (const row of ui.transcriptionItems.querySelectorAll("[data-plan-item]")) {
    const original = state.plan.items.find((item) => item.id === row.dataset.planItem);
    if (!original || !row.querySelector('[data-field="include"]').checked) continue;
    const searchTerm = row.querySelector('[data-field="search_term"]').value.trim();
    const quantity = Number(row.querySelector('[data-field="quantity"]').value);
    if (!searchTerm || !Number.isFinite(quantity) || quantity <= 0) {
      throw new Error("Every included line needs a product name and positive quantity.");
    }
    items.push({
      ...original,
      search_term: searchTerm,
      context: row.querySelector('[data-field="context"]').value.trim(),
      quantity,
      unit: row.querySelector('[data-field="unit"]').value,
      needs_review: false,
      confirmed: true,
    });
  }
  if (!items.length) throw new Error("Include at least one reviewed grocery item.");
  return { ...state.plan, items };
}

function contractLevelOptions(selected) {
  return [
    ["required", "Required"],
    ["preferred", "Preferred"],
    ["flexible", "Flexible"],
  ].map(([value, label]) =>
    `<option value="${value}" ${value === selected ? "selected" : ""}>${label}</option>`
  ).join("");
}

function contractPolicyOptions(selected) {
  return [
    ["none", "No substitution"],
    ["same_brand", "Same brand"],
    ["equivalent", "Equivalent product"],
    ["any", "Any reasonable match"],
  ].map(([value, label]) =>
    `<option value="${value}" ${value === selected ? "selected" : ""}>${label}</option>`
  ).join("");
}

function contractItemMarkup(item) {
  return `
    <article class="contract-item" data-contract-item="${escapeHtml(item.planned_item_id)}">
      <div class="contract-item-copy">
        <strong>${escapeHtml(item.product_name)}</strong>
        <small>${escapeHtml(`${item.quantity} ${item.unit}`)} requested</small>
      </div>
      <label class="contract-field">
        <span>Quantity rule</span>
        <select data-contract-field="quantity_level">
          ${contractLevelOptions(item.quantity_level)}
        </select>
      </label>
      <label class="contract-field">
        <span>Brand</span>
        <input data-contract-field="brand" value="${escapeHtml(item.brand || "")}" placeholder="Any brand" />
      </label>
      <label class="contract-field">
        <span>Brand rule</span>
        <select data-contract-field="brand_level">
          ${contractLevelOptions(item.brand_level)}
        </select>
      </label>
      <div class="contract-tolerance">
        <label class="contract-field">
          <span>Substitutions</span>
          <select data-contract-field="substitution_policy">
            ${contractPolicyOptions(item.substitution_policy)}
          </select>
        </label>
        <label class="contract-field">
          <span>Minimum quantity supplied (%)</span>
          <input data-contract-field="min_fill_ratio" type="number" min="1" max="500" step="1" value="${escapeHtml(Math.round(item.min_fill_ratio * 100))}" />
        </label>
        <label class="contract-field">
          <span>Maximum quantity supplied (%)</span>
          <input data-contract-field="max_fill_ratio" type="number" min="100" max="1000" step="1" value="${escapeHtml(Math.round(item.max_fill_ratio * 100))}" />
        </label>
        <label class="contract-field">
          <span>Maximum line price (optional)</span>
          <input data-contract-field="item_price_cap" type="number" min="1" step="1" inputmode="decimal" value="${escapeHtml(item.item_price_cap ?? "")}" placeholder="No item cap" />
        </label>
      </div>
    </article>`;
}

function renderContract() {
  if (!state.contract) return;
  ui.contractItems.innerHTML = state.contract.items.map(contractItemMarkup).join("");
  ui.contractBudget.value = state.contract.cart_budget ?? "";
  ui.contractNote.textContent =
    `Contract v${state.contract.version} is a draft. Confirm it before any provider search.`;
  ui.transcription.hidden = true;
  ui.progress.hidden = true;
  ui.contract.hidden = false;
  ui.contract.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function beginCartProof(plan) {
  state.plan = plan;
  ui.contract.hidden = true;
  ui.progress.hidden = false;
  ui.progressMessage.textContent = "Preparing one contract for every selected app…";
  setStage("planner");
  try {
    const response = await fetch("/api/contracts/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(plan),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "The shopping contract could not be created.");
    state.contract = payload;
    renderContract();
    return true;
  } catch (error) {
    ui.progressMessage.textContent = error.message;
    showToast(error.message, { tone: "error", sticky: true });
    return false;
  }
}

function contractConfirmationPayload() {
  if (!state.contract) throw new Error("Create a shopping contract first.");
  const items = state.contract.items.map((item) => {
    const row = ui.contractItems.querySelector(
      `[data-contract-item="${CSS.escape(item.planned_item_id)}"]`,
    );
    const numberValue = (field) => {
      const raw = row.querySelector(`[data-contract-field="${field}"]`).value.trim();
      return raw ? Number(raw) : null;
    };
    const minPercent = numberValue("min_fill_ratio");
    const maxPercent = numberValue("max_fill_ratio");
    if (
      !Number.isFinite(minPercent)
      || !Number.isFinite(maxPercent)
      || minPercent <= 0
      || maxPercent < minPercent
    ) {
      throw new Error(`Check the quantity tolerance for ${item.product_name}.`);
    }
    const cap = numberValue("item_price_cap");
    if (cap != null && (!Number.isFinite(cap) || cap <= 0)) {
      throw new Error(`Check the price cap for ${item.product_name}.`);
    }
    return {
      ...item,
      quantity_level: row.querySelector('[data-contract-field="quantity_level"]').value,
      brand: row.querySelector('[data-contract-field="brand"]').value.trim() || null,
      brand_level: row.querySelector('[data-contract-field="brand_level"]').value,
      substitution_policy: row.querySelector('[data-contract-field="substitution_policy"]').value,
      min_fill_ratio: minPercent / 100,
      max_fill_ratio: maxPercent / 100,
      item_price_cap: cap,
    };
  });
  const budgetRaw = ui.contractBudget.value.trim();
  const budget = budgetRaw ? Number(budgetRaw) : null;
  if (budget != null && (!Number.isFinite(budget) || budget <= 0)) {
    throw new Error("The final cart budget must be a positive amount.");
  }
  return {
    version: state.contract.version,
    items,
    cart_budget: budget,
  };
}

async function confirmCartProofContract() {
  if (!state.contract || !state.plan) return;
  setButtonState(ui.confirmContract, "loading");
  try {
    const response = await fetch(
      `/api/contracts/${encodeURIComponent(state.contract.id)}/confirm`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(contractConfirmationPayload()),
      },
    );
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "The shopping contract could not be confirmed.");
    state.contract = payload;
    ui.contractNote.textContent =
      `Contract v${payload.version} confirmed · ${payload.fingerprint.slice(0, 12)}. Checking every cart now.`;
    const succeeded = await runComparison(state.plan, payload);
    if (!succeeded) throw new Error("The confirmed contract could not be compared.");
    setButtonState(ui.confirmContract, "success");
  } catch (error) {
    setButtonState(ui.confirmContract, "error");
    showToast(error.message, { tone: "error", sticky: true });
    window.setTimeout(() => setButtonState(ui.confirmContract), 1800);
  }
}

async function previewRequest(action, { useCloud = false } = {}) {
  const hasText = Boolean(ui.text.value.trim());
  const hasImage = Boolean(ui.image.files[0]);
  if (!hasText && !hasImage) {
    showRequestError("Add a photo or type at least one grocery item.");
    ui.text.focus();
    return;
  }
  if (hasImage && state.photoQuality?.status === "checking") {
    showRequestError("Wait a moment while the photo quality check finishes.");
    return;
  }
  if (hasImage && state.photoQuality?.status === "retake") {
    showRequestError(
      state.photoQuality.guidance?.join(" ")
      || "Retake this photo in brighter, steadier conditions before continuing.",
    );
    return;
  }
  state.pendingAction = action;
  const button = useCloud
    ? ui.cloudRetry
    : action === "compare" ? ui.compareButton : ui.draftButton;
  showRequestError("");
  setButtonState(button, "loading");
  ui.progress.hidden = false;
  ui.review.hidden = true;
  ui.contract.hidden = true;
  ui.comparison.hidden = true;
  ui.confirmBar.hidden = true;
  ui.progressMessage.textContent = useCloud
    ? "Retrying only uncertain line crops with cloud vision…"
    : "Reading and structuring the request locally…";
  setStage("planner");
  const formData = new FormData();
  formData.append("text", ui.text.value);
  formData.append("use_cloud", useCloud ? "true" : "false");
  formData.append(
    "provider_ids",
    (
      action === "compare"
        ? selectedComparisonProviders()
        : [state.provider]
    ).join(","),
  );
  if (hasImage) formData.append("image", ui.image.files[0]);
  try {
    const response = await fetch("/api/plans/preview", { method: "POST", body: formData });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "Transcription failed.");
    state.plan = payload;
    if (state.recognitionPolicy === "autonomous_safe" && !useCloud) {
      if (action === "draft" && !state.providerReady && !state.demoMode) {
        throw new Error(`Connect ${state.providerName} before searching the list.`);
      }
      const succeeded = action === "compare"
        ? await beginCartProof(payload)
        : await streamDraft(payload);
      if (!succeeded) return;
      return;
    }
    renderTranscription();
    setButtonState(button, "success");
    window.setTimeout(() => setButtonState(button), 1200);
  } catch (error) {
    setButtonState(button, "error");
    ui.progressMessage.textContent = error.message;
    showToast(error.message, { tone: "error", sticky: true });
    window.setTimeout(() => setButtonState(button), 1800);
  }
}

function friendlyComparisonReason(reason) {
  const cleanReason = reason.trim().replace(/\.+$/, "");
  const unverified = cleanReason.match(/^(.+?) could not verify the quantity for:\s*(.+)$/i);
  if (unverified) {
    return `No size was written for ${unverified[2]}. The ${unverified[1]} total uses the selected packs shown below.`;
  }
  const short = cleanReason.match(/^(.+?) supplies short packs for:\s*(.+)$/i);
  if (short) {
    return `The selected ${short[1]} packs may not supply the full requested amount for ${short[2]}.`;
  }
  const missing = cleanReason.match(/^(.+?) is missing:\s*(.+)$/i);
  if (missing) {
    return `${missing[1]} did not find comparable products for ${missing[2]}. Its partial total is not ranked as the best option.`;
  }
  return cleanReason
    .replaceAll("Verified-mode", "Exact-total")
    .replaceAll("verified-mode", "exact-total");
}

function proofCheckMarkup(check) {
  const label = check.status === "pass"
    ? "Pass"
    : check.status === "warning"
      ? "Review"
      : check.status === "unverified"
        ? "Unverified"
        : "Fail";
  const dotClass = check.status === "pass"
    ? "is-pass"
    : check.status === "warning"
      ? "is-warning"
      : "is-fail";
  return `
    <li>
      <strong class="proof-status"><i class="proof-dot ${dotClass}"></i>${label}</strong>
      <span>${escapeHtml(check.explanation)}</span>
    </li>`;
}

function platformProofMarkup(proof) {
  if (!proof) return "";
  const label = proof.status === "compliant"
    ? "All confirmed requirements pass"
    : proof.status === "qualified"
      ? `${proof.preference_misses} preferred choice${proof.preference_misses === 1 ? "" : "s"} changed`
      : `${proof.required_failures} required check${proof.required_failures === 1 ? "" : "s"} did not pass`;
  const itemProofs = proof.item_proofs.map((item) => `
    <details class="proof-item">
      <summary>${escapeHtml(item.requested_item)} · ${escapeHtml(item.status)}</summary>
      <ul class="proof-checks">${item.checks.map(proofCheckMarkup).join("")}</ul>
    </details>`).join("");
  const basket = proof.basket_checks.length
    ? `<details class="proof-item">
        <summary>Cart total · ${proof.basket_checks.some((check) => check.status !== "pass") ? "check" : "pass"}</summary>
        <ul class="proof-checks">${proof.basket_checks.map(proofCheckMarkup).join("")}</ul>
      </details>`
    : "";
  return `
    <section class="proof-summary is-${escapeHtml(proof.status)}">
      <strong>CartProof: ${escapeHtml(label)}</strong>
      <div class="proof-items">${itemProofs}${basket}</div>
    </section>`;
}

function comparisonOutcomeMarkup(outcome, winner) {
  const failed = outcome.status !== "ok" || !outcome.summary;
  if (failed) {
    return `
      <article class="platform-outcome is-failed">
        <header>
          <span class="comparison-status">${escapeHtml(outcome.status.replaceAll("_", " "))}</span>
          <h3>${escapeHtml(outcome.display_name)}</h3>
        </header>
        <div class="coverage-warnings">
          <p>${escapeHtml(outcome.error || "This platform could not be compared.")}</p>
        </div>
        <footer><span>Not ranked</span></footer>
      </article>`;
  }

  const summary = outcome.summary;
  const draft = state.comparisonProposal?.drafts?.[outcome.provider];
  const itemLines = summary.lines.map((line) => {
    const draftItem = draft?.items?.find(
      (item) => item.selected_product_id === line.product_id,
    );
    const editor = draftItem && summary.estimated
      ? `<span class="comparison-product-editor">
          <select
            class="comparison-product-select"
            data-provider="${escapeHtml(outcome.provider)}"
            data-item="${escapeHtml(draftItem.planned.id)}"
            aria-label="Product for ${escapeHtml(draftItem.planned.search_term)} on ${escapeHtml(outcome.display_name)}"
          >
            ${draftItem.candidates.filter((candidate) => candidate.in_stock).map((candidate) =>
              `<option value="${escapeHtml(candidate.id)}" ${candidate.id === draftItem.selected_product_id ? "selected" : ""}>${escapeHtml(candidate.name)} · ${escapeHtml(candidate.pack_size || "pack")} · ${money.format(candidate.price)}</option>`,
            ).join("")}
          </select>
          <input
            class="comparison-units"
            type="number"
            min="1"
            max="50"
            value="${escapeHtml(draftItem.units_to_add)}"
            data-provider="${escapeHtml(outcome.provider)}"
            data-item="${escapeHtml(draftItem.planned.id)}"
            aria-label="Packs for ${escapeHtml(draftItem.planned.search_term)} on ${escapeHtml(outcome.display_name)}"
          />
        </span>`
      : "";
    // The amount a platform actually supplies is the whole basis of the
    // comparison: "2 × 250 g" and "1 × 500 g" cost different amounts for the
    // same groceries. Pack size used to appear only inside truncated dropdown
    // text, which is where a 250 g against 500 g mismatch stayed invisible.
    const selected = draftItem?.candidates?.find(
      (candidate) => candidate.id === line.product_id,
    );
    const supply = selected?.pack_size
      ? `${escapeHtml(line.quantity)} × ${escapeHtml(selected.pack_size)}`
      : `${escapeHtml(line.quantity)} × pack`;
    return `
    <li>
      <span>
        <strong>${escapeHtml(line.name)}</strong>
        <small>${supply} · ${money.format(line.unit_price)} each</small>
        ${editor}
      </span>
      <span>${money.format(line.line_total)}</span>
    </li>`;
  }).join("");

  // A platform with no comparable product is not the same as a platform that
  // simply lost on price, and it must not read as an empty gap in the column.
  const unmatchedLines = (draft?.items ?? [])
    .filter((item) => !item.removed && !item.selected_product_id)
    .map((item) => `
    <li class="comparison-line-unmatched">
      <span>
        <strong>${escapeHtml(item.planned.search_term)}</strong>
        <small>${escapeHtml(item.reason || "No comparable product on this platform.")}</small>
      </span>
      <span>—</span>
    </li>`).join("");
  const feeLines = summary.fees.map((fee) => `
    <li><span>${escapeHtml(fee.label)}</span><span>${money.format(fee.amount)}</span></li>`
  ).join("");
  const warnings = [
    ...(outcome.missing_items ?? []).map((item) => `No matching product was found for ${item}.`),
    ...(outcome.partial_items ?? []).map((item) => `The selected pack may not supply the full requested amount for ${item}.`),
    ...(outcome.unverified_items ?? []).map((item) => `No size was written for ${item}, so the selected pack is an estimate.`),
  ];
  const selectedCount = summary.lines.length;
  const coverage = warnings.length
    ? `${selectedCount} product${selectedCount === 1 ? "" : "s"} selected · ${warnings.length} detail${warnings.length === 1 ? "" : "s"} to check`
    : `${selectedCount} product${selectedCount === 1 ? "" : "s"} selected · complete list`;
  return `
    <article class="platform-outcome${outcome.provider === winner ? " is-winner" : ""}">
      <header>
        <span class="comparison-status">${outcome.provider === winner ? "CartProof choice" : "Compared"}</span>
        <h3>${escapeHtml(outcome.display_name)}</h3>
        <span class="comparison-coverage">${escapeHtml(coverage)}</span>
        ${summary.estimated ? '<span class="comparison-estimate">Estimated total</span>' : '<span class="comparison-status">Exact cart total</span>'}
      </header>
      <ul class="comparison-lines">${itemLines + unmatchedLines || "<li><span>No matched products</span><span>—</span></li>"}</ul>
      ${warnings.length ? `<div class="coverage-warnings">${warnings.map((warning) => `<p>${escapeHtml(warning)}</p>`).join("")}</div>` : ""}
      ${platformProofMarkup(outcome.proof)}
      <ul class="bill-lines">
        <li><span>Item subtotal</span><span>${money.format(summary.subtotal)}</span></li>
        ${feeLines}
        <li><span>Final total</span><span>${money.format(summary.total)}</span></li>
      </ul>
      <footer>
        <span>${summary.delivery_eta_minutes != null ? `${escapeHtml(summary.delivery_eta_minutes)} min delivery` : "Delivery time unavailable"}</span>
        ${summary.raw_note ? `<small>${escapeHtml(summary.raw_note)}</small>` : ""}
      </footer>
    </article>`;
}

function renderComparison(report) {
  const winner = report.winner;
  const winnerOutcome = report.platforms.find((outcome) => outcome.provider === winner);
  ui.comparisonWinner.innerHTML = winnerOutcome
    ? `<span>CartProof recommendation</span><strong>${escapeHtml(winnerOutcome.display_name)}</strong><span>${money.format(winnerOutcome.summary.total)}</span>`
    : `<span>CartProof result</span><strong>No compliant cart</strong><span>Review required</span>`;
  ui.comparisonSummary.textContent = report.estimated
    ? state.demoMode
      ? `Contract v${report.contract_version ?? 1} checked against local demo catalogues and estimated fees. No cart was changed.`
      : `Contract v${report.contract_version ?? 1} checked against live product prices and estimated fees. No cart was changed.`
    : `Contract v${report.contract_version ?? 1} checked against exact shopping-app cart totals and disclosed fees.`;
  ui.comparisonEditHelp.hidden = !report.estimated;
  ui.comparisonReasons.innerHTML = (report.reasons ?? [])
    .map((reason) => `<p>${escapeHtml(friendlyComparisonReason(reason))}</p>`)
    .join("");
  ui.comparisonReasonDetails.hidden = !(report.reasons ?? []).length;
  ui.comparisonReasonDetails.open = false;
  ui.comparisonGrid.innerHTML = report.platforms
    .map((outcome) => comparisonOutcomeMarkup(outcome, winner))
    .join("");
  ui.progress.hidden = true;
  ui.comparison.hidden = false;
  ui.comparisonDecision.hidden = !state.comparisonOperation;
  ui.comparison.scrollIntoView({ behavior: "smooth", block: "start" });
}

async function overrideComparisonSelection(providerId, itemId, productId, units) {
  if (!state.comparisonProposal) return;
  try {
    const response = await fetch(
      `/api/comparisons/proposals/${encodeURIComponent(state.comparisonProposal.id)}/override`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          provider_id: providerId,
          planned_item_id: itemId,
          product_id: productId,
          units_to_add: Math.max(1, Math.min(50, Number(units) || 1)),
        }),
      },
    );
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "Comparison choice failed.");
    state.comparisonProposal = payload;
    renderComparison(payload.report);
  } catch (error) {
    showToast(error.message, { tone: "error", sticky: true });
  }
}

async function runComparison(plan, contract = state.contract) {
  const providerIds = selectedComparisonProviders();
  if (!providerIds.length) {
    showToast("Choose at least one app to compare.", { tone: "error" });
    return false;
  }

  showRequestError("");
  setButtonState(ui.compareButton, "loading");
  ui.progress.hidden = false;
  ui.contract.hidden = true;
  ui.review.hidden = true;
  ui.confirmBar.hidden = true;
  ui.comparison.hidden = true;
  ui.progressMessage.textContent = "Planning once, then searching connected apps…";
  setStage("planner");

  const formData = new FormData();
  formData.append("plan_json", JSON.stringify(plan));
  formData.append("provider_ids", providerIds.join(","));
  if (contract?.id) formData.append("contract_id", contract.id);

  try {
    const response = await fetch("/api/comparisons/estimate", {
      method: "POST",
      body: formData,
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "Comparison failed.");
    state.comparisonProposal = payload;
    state.comparisonOperation = null;
    ui.progressMessage.textContent = "Estimated comparison ready.";
    completeStages();
    renderComparison(payload.report);
    setButtonState(ui.compareButton, "success");
    window.setTimeout(() => setButtonState(ui.compareButton), 1200);
    return true;
  } catch (error) {
    setButtonState(ui.compareButton, "error");
    ui.progressMessage.textContent = error.message;
    showToast(error.message, { tone: "error", sticky: true });
    window.setTimeout(() => setButtonState(ui.compareButton), 1800);
    return false;
  }
}

async function verifyComparisonReadiness() {
  if (!state.comparisonProposal) return;
  setButtonState(ui.verifyComparison, "loading");
  try {
    const preflightResponse = await fetch(
      `/api/comparisons/proposals/${encodeURIComponent(state.comparisonProposal.id)}/verify-preflight`,
      { method: "POST" },
    );
    const preflight = await preflightResponse.json();
    if (!preflightResponse.ok) {
      throw new Error(preflight.detail || "The exact-total check could not start.");
    }
    if (!preflight.can_continue || !preflight.confirmation_token) {
      const details = preflight.platforms
        .filter((platform) => !platform.eligible)
        .map((platform) => `${platform.display_name}: ${platform.message}`)
        .join(" ");
      throw new Error(details || "The exact-total check is not ready.");
    }
    const confirmed = window.confirm(
      "To read exact fees, this will temporarily add the reviewed products to every eligible cart. Each cart must be empty. Checkout and payment will not be opened. Continue?",
    );
    if (!confirmed) return;

    const verifyResponse = await fetch(
      `/api/comparisons/proposals/${encodeURIComponent(state.comparisonProposal.id)}/verify`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirmation_token: preflight.confirmation_token }),
      },
    );
    const operation = await verifyResponse.json();
    if (!verifyResponse.ok) {
      throw new Error(operation.detail || "Verified comparison failed.");
    }
    state.comparisonOperation = operation;
    renderComparison(operation.report);
  } catch (error) {
    showToast(error.message, { tone: "error", sticky: true });
  } finally {
    setButtonState(ui.verifyComparison);
  }
}

async function chooseComparisonAction(action) {
  if (!state.comparisonOperation) return;
  const response = await fetch(
    `/api/comparisons/${encodeURIComponent(state.comparisonOperation.id)}/choose`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        action,
        winner: state.comparisonOperation.report.winner,
      }),
    },
  );
  const payload = await response.json();
  if (!response.ok) {
    showToast(payload.detail || "Cart cleanup failed.", { tone: "error", sticky: true });
    return;
  }
  state.comparisonOperation = payload;
  const failed = (payload.cleanup ?? []).filter((outcome) => !outcome.success);
  showToast(
    failed.length
      ? failed.map((outcome) => outcome.message).join(" ")
      : "Comparison cart choice applied. Checkout remains manual.",
    { tone: failed.length ? "error" : "default", sticky: Boolean(failed.length) },
  );
}

function findItem(element) {
  const id = element.closest("[data-item-id]")?.dataset.itemId;
  return state.draft?.items.find((item) => item.planned.id === id) ?? null;
}

async function researchItem(form) {
  const item = findItem(form);
  const input = form.querySelector(".query-input");
  const button = form.querySelector("button");
  const query = input.value.trim();
  if (!item || !query) {
    input.setAttribute("aria-invalid", "true");
    return;
  }
  input.setAttribute("aria-invalid", "false");
  button.dataset.state = "loading";
  button.disabled = true;
  button.textContent = "Searching…";
  try {
    const response = await fetch("/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        draft_id: state.draft.id,
        planned_item_id: item.planned.id,
        query,
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || `${state.providerName} search failed.`);
    const index = state.draft.items.findIndex((entry) => entry.planned.id === item.planned.id);
    state.draft.items[index] = payload;
    delete button.dataset.state;
    renderDraft();
  } catch (error) {
    input.setAttribute("aria-invalid", "true");
    showToast(error.message, { tone: "error" });
    button.disabled = false;
    button.dataset.state = "error";
    button.textContent = "Search";
    window.setTimeout(() => delete button.dataset.state, 1800);
  }
}

function openSummary(payload) {
  ui.summaryCopy.textContent = payload.dry_run
    ? `${payload.succeeded} selection${payload.succeeded === 1 ? "" : "s"} checked. Preview mode did not click Add.`
    : `${payload.succeeded} item${payload.succeeded === 1 ? "" : "s"} added to ${state.providerName}; ${payload.failed} failed. Checkout was not opened.`;
  ui.summaryList.innerHTML = payload.results.map((result) =>
    `<li class="${result.success ? "" : "is-error"}"><strong>${result.success ? (payload.dry_run ? "Checked" : "Added") : "Failed"}</strong> · ${escapeHtml(result.message)}</li>`,
  ).join("");
  document.querySelector("#app-shell").inert = true;
  ui.dialog.showModal();
  ui.summaryDone.focus();
}

function closeSummary() {
  ui.dialog.close();
  document.querySelector("#app-shell").inert = false;
  ui.confirmButton.focus();
}

async function confirmDraft() {
  const items = activeItems();
  if (!items.length) return;
  setButtonState(ui.confirmButton, "loading");
  try {
    const response = await fetch("/api/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        draft_id: state.draft.id,
        selections: items.map((item) => ({
          planned_item_id: item.planned.id,
          product_id: item.selected_product_id,
          units_to_add: item.units_to_add,
        })),
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "Cart assembly failed.");
    setButtonState(ui.confirmButton, "success");
    openSummary(payload);
    window.setTimeout(() => setButtonState(ui.confirmButton), 1200);
  } catch (error) {
    setButtonState(ui.confirmButton, "error");
    showToast(error.message, { tone: "error", sticky: true });
    window.setTimeout(() => setButtonState(ui.confirmButton), 1800);
  }
}

async function streamDraft(plan) {
  ui.toastStack.replaceChildren();
  showRequestError("");
  setButtonState(ui.draftButton, "loading");
  ui.progress.hidden = false;
  ui.transcription.hidden = true;
  ui.review.hidden = true;
  ui.confirmBar.hidden = true;
  ui.progressMessage.textContent = "Starting the planner…";
  setStage("planner");
  ui.progress.scrollIntoView({ behavior: "smooth", block: "center" });

  const formData = new FormData();
  formData.append("provider_id", state.provider);
  formData.append("plan_json", JSON.stringify(plan));

  try {
    const response = await fetch("/api/drafts/stream", { method: "POST", body: formData });
    if (!response.ok || !response.body) {
      let detail = "The draft request failed.";
      try { detail = (await response.json()).detail || detail; } catch { /* non-JSON response */ }
      throw new Error(detail);
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done });
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        if (!line.trim()) continue;
        const message = JSON.parse(line);
        if (message.event === "error") throw new Error(message.message);
        ui.progressMessage.textContent = message.message;
        setStage(message.stage);
        if (message.event === "draft") state.draft = message.data;
      }
      if (done) break;
    }
    if (!state.draft) throw new Error("The pipeline finished without a draft cart.");
    completeStages();
    renderDraft();
    ui.review.hidden = false;
    ui.confirmBar.hidden = state.autoAdd;
    ui.review.scrollIntoView({ behavior: "smooth", block: "start" });
    setButtonState(ui.draftButton, "success");
    window.setTimeout(() => setButtonState(ui.draftButton), 1200);
    return true;
  } catch (error) {
    setButtonState(ui.draftButton, "error");
    ui.progressMessage.textContent = error.message;
    showToast(error.message, { tone: "error", sticky: true });
    window.setTimeout(() => setButtonState(ui.draftButton), 1800);
    return false;
  }
}

async function buildDraft(event) {
  event.preventDefault();
  await previewRequest("draft");
}

async function continueReviewedPlan() {
  try {
    const plan = reviewedPlanFromForm();
    if (state.pendingAction === "draft" && !state.providerReady && !state.demoMode) {
      throw new Error(`Connect ${state.providerName} before searching the reviewed list.`);
    }
    setButtonState(ui.continueReviewed, "loading");
    const succeeded = state.pendingAction === "compare"
      ? await beginCartProof(plan)
      : await streamDraft(plan);
    if (!succeeded) {
      setButtonState(ui.continueReviewed, "error");
      window.setTimeout(() => setButtonState(ui.continueReviewed), 1800);
      return;
    }
    setButtonState(ui.continueReviewed, "success");
    window.setTimeout(() => setButtonState(ui.continueReviewed), 1200);
  } catch (error) {
    setButtonState(ui.continueReviewed, "error");
    showToast(error.message, { tone: "error", sticky: true });
    window.setTimeout(() => setButtonState(ui.continueReviewed), 1800);
  }
}

ui.image.addEventListener("change", async () => {
  const file = ui.image.files[0];
  ui.fileName.textContent = file ? file.name : "No photo selected";
  ui.uploadBox.classList.toggle("is-success", Boolean(file));
  ui.uploadBox.classList.remove("is-error");
  state.plan = null;
  state.contract = null;
  ui.transcription.hidden = true;
  ui.contract.hidden = true;
  state.photoQualityController?.abort();
  state.photoQuality = null;
  ui.photoQuality.hidden = true;
  delete ui.photoQuality.dataset.status;
  if (!file) return;

  const controller = new AbortController();
  state.photoQualityController = controller;
  state.photoQuality = { status: "checking" };
  ui.photoQuality.hidden = false;
  ui.photoQuality.textContent = "Checking lighting, focus, resolution, and page angle…";
  try {
    const formData = new FormData();
    formData.append("image", file);
    const response = await fetch("/api/images/quality", {
      method: "POST",
      body: formData,
      signal: controller.signal,
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "Photo quality check failed.");
    state.photoQuality = payload;
    ui.photoQuality.dataset.status = payload.status;
    if (payload.status === "good") {
      ui.photoQuality.textContent = "Photo quality looks good for handwriting recognition.";
    } else if (payload.status === "usable") {
      ui.photoQuality.textContent = payload.guidance.join(" ")
        || "This photo is usable, but a clearer retake may improve recognition.";
    } else {
      ui.photoQuality.textContent = `Retake recommended. ${payload.guidance.join(" ")}`;
      ui.uploadBox.classList.remove("is-success");
      ui.uploadBox.classList.add("is-error");
    }
  } catch (error) {
    if (error.name === "AbortError") return;
    state.photoQuality = null;
    ui.photoQuality.dataset.status = "usable";
    ui.photoQuality.textContent = "Quality check unavailable; the photo will still be validated before recognition.";
  }
});

ui.form.addEventListener("submit", buildDraft);
ui.compareButton.addEventListener("click", () => previewRequest("compare"));
ui.cloudRetry.addEventListener("click", () =>
  previewRequest(state.pendingAction, { useCloud: true })
);
ui.addTranscriptionItem.addEventListener("click", addMissingTranscriptionItem);
ui.continueReviewed.addEventListener("click", continueReviewedPlan);
ui.confirmContract.addEventListener("click", confirmCartProofContract);
ui.transcriptionItems.addEventListener("input", (event) => {
  const row = event.target.closest("[data-plan-item]");
  if (!row || event.target.matches('[data-field="include"]')) return;
  row.querySelector('[data-field="include"]').checked = true;
  row.classList.remove("is-review");
});
ui.verifyComparison.addEventListener("click", verifyComparisonReadiness);
ui.comparisonDecision.addEventListener("click", (event) => {
  const button = event.target.closest("[data-comparison-action]");
  if (button) chooseComparisonAction(button.dataset.comparisonAction);
});
ui.comparisonGrid.addEventListener("change", (event) => {
  const control = event.target.closest(".comparison-product-select, .comparison-units");
  if (!control) return;
  const providerId = control.dataset.provider;
  const itemId = control.dataset.item;
  const matching = [...ui.comparisonGrid.querySelectorAll(
    ".comparison-product-select, .comparison-units",
  )].filter(
    (candidate) =>
      candidate.dataset.provider === providerId
      && candidate.dataset.item === itemId,
  );
  const select = matching.find((candidate) =>
    candidate.classList.contains("comparison-product-select")
  );
  const units = matching.find((candidate) =>
    candidate.classList.contains("comparison-units")
  );
  if (select && units) {
    overrideComparisonSelection(providerId, itemId, select.value, units.value);
  }
});

ui.loginButton.addEventListener("click", async () => {
  setButtonState(ui.loginButton, "loading");
  try {
    const response = await fetch(
      `/api/providers/connect?provider=${encodeURIComponent(state.provider)}`,
      { method: "POST" },
    );
    let payload = {};
    try {
      payload = await response.json();
    } catch {
      // Keep the fallback below when a proxy or server returns a non-JSON error page.
    }
    if (!response.ok) throw new Error(payload.detail || `${state.providerName} connection failed.`);
    if (payload.authorization_url) {
      window.location.assign(payload.authorization_url);
      return;
    }
    await loadProviderStatus(true);
  } catch (error) {
    setButtonState(ui.loginButton, "error");
    showToast(error.message, { tone: "error", sticky: true });
  }
});

ui.addressSelect.addEventListener("change", async () => {
  const addressId = ui.addressSelect.value;
  if (!addressId) return;
  ui.addressSelect.disabled = true;
  try {
    const response = await fetch(
      `/api/providers/address?provider=${encodeURIComponent(state.provider)}`,
      {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ address_id: addressId }),
      },
    );
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "Address selection failed.");
    applyProviderStatus(payload);
  } catch (error) {
    showToast(error.message, { tone: "error", sticky: true });
  } finally {
    ui.addressSelect.disabled = false;
  }
});

ui.refreshAddresses.addEventListener("click", async () => {
  setButtonState(ui.refreshAddresses, "loading");
  try {
    await loadProviderStatus(true);
    const addressCount = ui.addressSelect.options.length;
    if (addressCount === 0) {
      showToast("No saved address found yet. Add one in Swiggy, then refresh again.", {
        tone: "error",
        sticky: true,
      });
    }
  } catch (error) {
    showToast(error.message, { tone: "error", sticky: true });
  } finally {
    setButtonState(ui.refreshAddresses);
  }
});

function clearDraftForProviderChange() {
  state.draft = null;
  state.comparisonProposal = null;
  state.comparisonOperation = null;
  state.contract = null;
  state.providerReady = false;
  state.providerStatusMessage = "";
  state.lastTotal = 0;
  ui.progress.hidden = true;
  ui.review.hidden = true;
  ui.transcription.hidden = true;
  ui.contract.hidden = true;
  ui.confirmBar.hidden = true;
  ui.comparison.hidden = true;
  ui.groups.replaceChildren();
  ui.cartFlags.replaceChildren();
}

ui.providerSelect.addEventListener("change", async () => {
  const previousProvider = state.provider;
  const nextProvider = ui.providerSelect.value;
  if (!nextProvider || nextProvider === previousProvider) return;
  ui.providerSelect.disabled = true;
  try {
    const response = await fetch("/api/providers/select", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ provider_id: nextProvider }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "Provider selection failed.");
    clearDraftForProviderChange();
    // Re-read the persistent browser/OAuth session after changing providers.
    // Server restarts intentionally clear the backend's in-memory connection flag.
    await refreshApplicationState(true);
  } catch (error) {
    ui.providerSelect.value = previousProvider;
    showToast(error.message, { tone: "error", sticky: true });
  } finally {
    ui.providerSelect.disabled = state.demoMode;
  }
});

ui.groups.addEventListener("submit", (event) => {
  const form = event.target.closest('[data-action="research"]');
  if (!form) return;
  event.preventDefault();
  researchItem(form);
});

ui.groups.addEventListener("change", (event) => {
  const item = findItem(event.target);
  if (!item) return;
  if (event.target.matches('input[type="radio"]')) {
    item.selected_product_id = event.target.value;
    item.reason = "Alternative selected by you for this draft.";
    renderDraft();
  }
  if (event.target.matches(".qty-input")) {
    item.units_to_add = Math.max(1, Math.min(50, Number(event.target.value) || 1));
    event.target.value = item.units_to_add;
    updateSummary();
  }
});

ui.groups.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-action]");
  if (!button) return;
  const item = findItem(button);
  if (!item) return;
  const action = button.dataset.action;
  if (action === "remove") {
    item.removed = !item.removed;
    const removed = item.removed;
    renderDraft();
    if (removed) {
      showToast(`${item.planned.search_term} removed from the draft.`, {
        action: { label: "Undo", run: () => { item.removed = false; renderDraft(); } },
      });
    }
  }
  if (action === "increment" || action === "decrement") {
    const delta = action === "increment" ? 1 : -1;
    item.units_to_add = Math.max(1, Math.min(50, item.units_to_add + delta));
    renderDraft();
  }
});

ui.confirmButton.addEventListener("click", confirmDraft);
ui.closeSummary.addEventListener("click", closeSummary);
ui.summaryDone.addEventListener("click", closeSummary);
ui.dialog.addEventListener("click", (event) => {
  if (event.target === ui.dialog) closeSummary();
});

function applyHealth(health) {
  state.dryRun = health.dry_run;
  state.demoMode = health.demo_mode;
  state.cartMutationsAllowed = health.cart_mutations_allowed;
  state.autoAdd = health.auto_add_to_cart && state.cartMutationsAllowed;
  state.checkoutDisabled = health.checkout_disabled;
  state.provider = health.grocery_provider;
  state.providerName = health.provider_name;
  state.availableProviders = health.providers ?? [];
  state.cloudRetryAvailable = Boolean(health.cloud_retry_available);
  state.cloudRetryProvider = health.cloud_retry_provider || "cloud";
  state.recognitionPolicy = health.recognition_policy || "review";
  ui.cloudRetry.querySelector(".button-label").textContent =
    `Retry uncertain lines with ${state.cloudRetryProvider === "nvidia" ? "NVIDIA" : "cloud"} vision`;
  ui.dishCapability.textContent = health.model_backend === "local"
    ? state.recognitionPolicy === "autonomous_safe"
      ? "Reads lists on this device and checks the product catalogue"
      : "Reads grocery lists offline · dish names need online planning"
    : "Can turn dish names into ingredient lists";

  if (state.availableProviders.length) {
    ui.providerSelect.innerHTML = state.availableProviders.map((provider) =>
      `<option value="${escapeHtml(provider.id)}">${escapeHtml(provider.display_name)}</option>`,
    ).join("");
  }
  ui.providerSelect.value = state.provider;
  ui.providerSelect.disabled = state.demoMode;

  ui.modeBadge.textContent = health.demo_mode
    ? "Demo · carts unchanged"
    : !state.cartMutationsAllowed
      ? "Preview · cart unchanged"
    : health.safety_lock
      ? "Safety lock · cart unchanged"
      : health.dry_run
      ? "Dry run · cart unchanged"
      : health.auto_add_to_cart
      ? "Auto-add · checkout off"
      : "Review first · checkout off";
  ui.confirmMode.textContent = state.autoAdd
    ? `The products above were added automatically to ${state.providerName}. Checkout and payment stay manual.`
    : !state.cartMutationsAllowed
    ? "Preview mode cannot add products, open checkout, pay, or place an order."
    : `This button adds the selected products to ${state.providerName}. It does not open checkout.`;
  ui.confirmButton.querySelector(".button-label").textContent =
    state.cartMutationsAllowed ? "Add selected items" : "Run safe test";
  if (!health.model_configured) ui.modeBadge.textContent = "List planning is unavailable";
  updateActionCopy();
}

async function refreshApplicationState(refreshStatus = false) {
  const response = await fetch("/api/health");
  const health = await response.json();
  if (!response.ok) throw new Error(health.detail || "Backend health check failed.");
  applyHealth(health);
  if (health.demo_mode) {
    ui.loginButton.hidden = true;
    ui.draftButton.disabled = false;
    updateActionCopy();
    return;
  }
  await loadProviderStatus(refreshStatus);
}

async function initialise() {
  try {
    const params = new URLSearchParams(window.location.search);
    const requestedProvider = params.get("provider");
    if (requestedProvider === "blinkit" || requestedProvider === "instamart" || requestedProvider === "zepto") {
      const response = await fetch("/api/providers/select", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider_id: requestedProvider }),
      });
      if (!response.ok) throw new Error("The requested grocery provider is unavailable.");
    }
    // Persistent provider sessions survive a local-server restart, but the
    // backend's cached connection flags do not.
    await refreshApplicationState(true);
  } catch {
    ui.modeBadge.textContent = "App service is offline";
    ui.shopModeNote.textContent = "The local app service is not responding. Restart it, then reload this page.";
    ui.shopModeNote.classList.add("is-safe");
  }
}

function applyProviderStatus(status) {
  state.providerConnected = status.connected;
  state.providerStatusMessage = status.message || "";
  state.providerName = status.display_name || state.providerName;
  ui.loginButton.hidden = false;
  ui.loginButton.textContent = status.connected
    ? "Connected"
    : "Connect";
  ui.loginButton.setAttribute(
    "aria-label",
    status.connected
      ? `${state.providerName} connected`
      : `Connect ${state.providerName}`,
  );
  setButtonState(ui.loginButton, status.connected ? "success" : "default");
  ui.loginButton.disabled = status.connected;

  const addresses = status.addresses ?? [];
  ui.addressPicker.hidden = !status.connected || addresses.length === 0;
  ui.addressEmpty.hidden = !(
    state.provider === "instamart"
    && status.connected
    && addresses.length === 0
  );
  ui.addressSelect.innerHTML = [
    status.requires_address ? '<option value="">Choose an address</option>' : "",
    ...addresses.map((address) =>
      `<option value="${escapeHtml(address.id)}" ${address.id === status.selected_address_id ? "selected" : ""}>${escapeHtml(address.label)}${address.detail ? ` · ${escapeHtml(address.detail)}` : ""}</option>`,
    ),
  ].join("");
  const ready = status.connected
    && !status.requires_address
    && (state.provider !== "instamart" || Boolean(status.selected_address_id));
  state.providerReady = ready;
  // Local transcription review works without a provider connection. The
  // reviewed-list action checks readiness immediately before any provider call.
  ui.draftButton.disabled = false;
  updateActionCopy();
}

async function loadProviderStatus(refresh = false) {
  const response = await fetch(
    `/api/providers/status?provider=${encodeURIComponent(state.provider)}&refresh=${refresh ? "true" : "false"}`,
  );
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.detail || "Provider status failed.");
  applyProviderStatus(payload);

  const params = new URLSearchParams(window.location.search);
  const providerError = params.get("provider_error");
  if (providerError) showToast(providerError, { tone: "error", sticky: true });
  if (params.has("provider_connected") || providerError) {
    window.history.replaceState({}, "", window.location.pathname);
  }
}

initialise();
