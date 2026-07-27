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
  cartMutationsAllowed: false,
  availableProviders: [],
  lastTotal: 0,
  comparisonProposal: null,
  comparisonOperation: null,
  plan: null,
  pendingAction: "draft",
  cloudRetryAvailable: false,
  cloudRetryProvider: "cloud",
  recognitionPolicy: "review",
};

const ui = {
  form: document.querySelector("#request-form"),
  text: document.querySelector("#request-text"),
  image: document.querySelector("#request-image"),
  uploadBox: document.querySelector("#upload-box"),
  fileName: document.querySelector("#file-name"),
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
  progress: document.querySelector("#progress"),
  progressMessage: document.querySelector("#progress-message"),
  stages: [...document.querySelectorAll("#stage-list li")],
  transcription: document.querySelector("#transcription"),
  transcriptionItems: document.querySelector("#transcription-items"),
  transcriptionNotice: document.querySelector("#transcription-notice"),
  transcriptionSummary: document.querySelector("#transcription-summary"),
  cloudRetry: document.querySelector("#cloud-retry"),
  continueReviewed: document.querySelector("#continue-reviewed"),
  review: document.querySelector("#review"),
  groups: document.querySelector("#review-groups"),
  cartFlags: document.querySelector("#cart-flags"),
  comparison: document.querySelector("#comparison"),
  comparisonSummary: document.querySelector("#comparison-summary"),
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
  });
}

function completeStages() {
  ui.stages.forEach((element) => {
    element.classList.remove("is-active");
    element.classList.add("is-done");
  });
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
    ? '<span class="selection-status">Automatically selected</span>'
    : `<form class="query-editor" data-action="research">
        <label for="query-${escapeHtml(item.planned.id)}">Search query for ${escapeHtml(providerQuery)}</label>
        <input class="query-input" id="query-${escapeHtml(item.planned.id)}" value="${escapeHtml(providerQuery)}" />
        <button class="text-button" type="submit">Search</button>
      </form>
      <button class="text-button" type="button" data-action="remove">${item.removed ? "Restore" : "Remove"}</button>`;
  const quantity = state.autoAdd
    ? `<p class="auto-quantity">${escapeHtml(item.units_to_add)} pack${item.units_to_add === 1 ? "" : "s"} selected for automatic Add</p>`
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
        <input type="checkbox" data-field="include" ${warning && !item.confirmed ? "" : "checked"} />
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
      <p class="transcription-source">Read from “${escapeHtml(item.raw_text || item.search_term)}” · semantic confidence ${Math.round((item.confidence ?? 0) * 100)}%</p>
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
    state.pendingAction === "compare" ? "Compare the reviewed list" : "Search the reviewed list";
  ui.transcription.hidden = false;
  ui.progress.hidden = true;
  ui.transcription.scrollIntoView({ behavior: "smooth", block: "start" });
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

async function previewRequest(action, { useCloud = false } = {}) {
  const hasText = Boolean(ui.text.value.trim());
  const hasImage = Boolean(ui.image.files[0]);
  if (!hasText && !hasImage) {
    showRequestError("Add a photo or type the grocery list first.");
    ui.text.focus();
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
        ? await runComparison(payload)
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
    ...(outcome.missing_items ?? []).map((item) => `Missing: ${item}`),
    ...(outcome.partial_items ?? []).map((item) => `Short quantity: ${item}`),
    ...(outcome.unverified_items ?? []).map((item) => `Quantity not verified: ${item}`),
  ];
  const coverage = warnings.length
    ? `${outcome.matched_items} matched · ${warnings.length} needs attention`
    : `${outcome.matched_items} matched · full coverage`;
  return `
    <article class="platform-outcome${outcome.provider === winner ? " is-winner" : ""}">
      <header>
        <span class="comparison-status">${outcome.provider === winner ? "Recommended" : "Compared"}</span>
        <h3>${escapeHtml(outcome.display_name)}</h3>
        <span class="comparison-coverage">${escapeHtml(coverage)}</span>
        ${summary.estimated ? '<span class="comparison-estimate">Estimated fees</span>' : '<span class="comparison-status">Verified cart total</span>'}
      </header>
      <ul class="comparison-lines">${itemLines + unmatchedLines || "<li><span>No matched products</span><span>—</span></li>"}</ul>
      ${warnings.length ? `<div class="coverage-warnings">${warnings.map((warning) => `<p>${escapeHtml(warning)}</p>`).join("")}</div>` : ""}
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
    ? `<span>${report.estimated ? "Estimated best option" : "Verified best option"}</span><strong>${escapeHtml(winnerOutcome.display_name)}</strong><span>${money.format(winnerOutcome.summary.total)}</span>`
    : "";
  ui.comparisonSummary.textContent = report.estimated
    ? "Estimated totals use product prices plus clearly labelled fee estimates. No cart was changed."
    : "Totals were read from provider carts and checked against their fee breakdowns.";
  ui.comparisonReasons.innerHTML = (report.reasons ?? [])
    .map((reason) => `<p>${escapeHtml(reason)}</p>`)
    .join("");
  ui.comparisonGrid.innerHTML = report.platforms
    .map((outcome) => comparisonOutcomeMarkup(outcome, winner))
    .join("");
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
    showToast("Comparison updated. Verified-mode confirmation must be checked again.");
  } catch (error) {
    showToast(error.message, { tone: "error", sticky: true });
  }
}

async function runComparison(plan) {
  const providerIds = selectedComparisonProviders();
  if (!providerIds.length) {
    showToast("Choose at least one app to compare.", { tone: "error" });
    return;
  }

  showRequestError("");
  setButtonState(ui.compareButton, "loading");
  ui.progress.hidden = false;
  ui.review.hidden = true;
  ui.confirmBar.hidden = true;
  ui.comparison.hidden = true;
  ui.progressMessage.textContent = "Planning once, then searching connected apps…";
  setStage("planner");

  const formData = new FormData();
  formData.append("plan_json", JSON.stringify(plan));
  formData.append("provider_ids", providerIds.join(","));

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
      throw new Error(preflight.detail || "Verified-mode preflight failed.");
    }
    if (!preflight.can_continue || !preflight.confirmation_token) {
      const details = preflight.platforms
        .filter((platform) => !platform.eligible)
        .map((platform) => `${platform.display_name}: ${platform.message}`)
        .join(" ");
      throw new Error(details || "Verified comparison is not ready.");
    }
    const confirmed = window.confirm(
      "Verified comparison will temporarily add the reviewed items to every eligible cart. Carts must be empty. Checkout remains disabled. Continue?",
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
    showToast("Verified cart totals are ready. No checkout was attempted.");
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

function burstAt(element) {
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const rect = element.getBoundingClientRect();
  const burst = document.createElement("span");
  burst.className = "star-burst";
  burst.style.left = `${rect.left + rect.width / 2}px`;
  burst.style.top = `${rect.top}px`;
  document.body.append(burst);
  window.setTimeout(() => burst.remove(), 600);
}

function openSummary(payload) {
  ui.summaryCopy.textContent = payload.dry_run
    ? `Dry run complete. ${payload.succeeded} selection${payload.succeeded === 1 ? "" : "s"} passed without clicking Add.`
    : `${payload.succeeded} item${payload.succeeded === 1 ? "" : "s"} added; ${payload.failed} failed.`;
  ui.summaryList.innerHTML = payload.results.map((result) =>
    `<li class="${result.success ? "" : "is-error"}"><strong>${result.success ? "Ready" : "Failed"}</strong> · ${escapeHtml(result.message)}</li>`,
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
    burstAt(ui.confirmButton);
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
      ? await runComparison(plan)
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

ui.image.addEventListener("change", () => {
  const file = ui.image.files[0];
  ui.fileName.textContent = file ? file.name : "No photo selected";
  ui.uploadBox.classList.toggle("is-success", Boolean(file));
  ui.uploadBox.classList.remove("is-error");
  state.plan = null;
  ui.transcription.hidden = true;
});

ui.form.addEventListener("submit", buildDraft);
ui.compareButton.addEventListener("click", () => previewRequest("compare"));
ui.cloudRetry.addEventListener("click", () =>
  previewRequest(state.pendingAction, { useCloud: true })
);
ui.continueReviewed.addEventListener("click", continueReviewedPlan);
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
    showToast(`Delivery address changed for ${state.providerName}.`);
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
    if (addressCount > 0) {
      showToast("Swiggy delivery addresses refreshed.");
    } else {
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
  state.lastTotal = 0;
  ui.progress.hidden = true;
  ui.review.hidden = true;
  ui.transcription.hidden = true;
  ui.confirmBar.hidden = true;
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
    showToast(`Now shopping with ${state.providerName}.`);
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
      ? "Autonomous local + catalogue recognition"
      : "Offline grocery parsing · dish expansion needs hosted planning"
    : "Dish-to-ingredient expansion";

  if (state.availableProviders.length) {
    ui.providerSelect.innerHTML = state.availableProviders.map((provider) =>
      `<option value="${escapeHtml(provider.id)}">${escapeHtml(provider.display_name)}</option>`,
    ).join("");
  }
  ui.providerSelect.value = state.provider;
  ui.providerSelect.disabled = state.demoMode;

  ui.modeBadge.textContent = health.demo_mode
    ? "SAFE DEMO · PROVIDERS OFF"
    : !state.cartMutationsAllowed
      ? `${state.providerName.toUpperCase()} SAFE TEST · CART WRITES OFF`
    : health.safety_lock
      ? "SAFETY LOCK · CART CLICKS OFF"
      : health.dry_run
      ? "DRY RUN · ADD CLICKS OFF"
      : health.auto_add_to_cart
      ? `${state.providerName.toUpperCase()} AUTO ADD · CHECKOUT OFF`
      : "LIVE MODE · REVIEW CAREFULLY";
  ui.confirmMode.textContent = state.autoAdd
    ? `The best match is added automatically to ${state.providerName}. Checkout remains disabled.`
    : !state.cartMutationsAllowed
    ? "Safe test: cannot add, checkout, pay, or place an order."
    : `Adds to ${state.providerName} after this confirmation.`;
  ui.confirmButton.querySelector(".button-label").textContent =
    state.cartMutationsAllowed ? "Add selected items" : "Run safe test";
  if (!health.model_configured) ui.modeBadge.textContent = "HF TOKEN NEEDED";
}

async function refreshApplicationState(refreshStatus = false) {
  const response = await fetch("/api/health");
  const health = await response.json();
  if (!response.ok) throw new Error(health.detail || "Backend health check failed.");
  applyHealth(health);
  if (health.demo_mode) {
    ui.loginButton.textContent = "Providers disabled in demo";
    ui.loginButton.disabled = true;
    ui.draftButton.disabled = false;
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
    ui.modeBadge.textContent = "BACKEND OFFLINE";
  }
}

function applyProviderStatus(status) {
  state.providerConnected = status.connected;
  state.providerName = status.display_name || state.providerName;
  ui.loginButton.textContent = status.connected
    ? `${state.providerName} connected`
    : `Connect ${state.providerName}`;
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
  if (status.message && !ready) showRequestError(status.message);
  else if (state.providerConnected) showRequestError("");
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
  if (params.has("provider_connected")) {
    showToast(`${state.providerName} connected.`);
  }
  if (params.has("provider_connected") || providerError) {
    window.history.replaceState({}, "", window.location.pathname);
  }
}

initialise();
