/* ============================================
   LYRIQ REVIEW LAYER

   Mounts itself onto the page that is already there. Nothing in the template
   calls into this file; it wraps renderResult(), adds its own controls to the
   actions row, and owns the drawer. Load it after the page's own script.

   What it adds:
     · a switch choosing learned corrections or the plain model
     · a Follow menu: the roster of experts on top, the ones you are actually
       following at the bottom. Tick any number of them.
     · a provenance line under each reading saying which one produced it
     · Edit this reading, which opens the correction drawer
     · a review log: who changed what, expert standings, retire and restore

   Every menu here is drawn by the page. Windows hands the option list of a
   native <select> to the OS, which ignores the page's colours and renders
   unreadable pale strips on a dark theme.
============================================ */

(function () {
    "use strict";

    var API = "/api/review";

    var LABELS = [
        "joy", "love", "longing", "sadness", "grief", "nostalgia",
        "anger", "fear", "anxiety", "peace", "devotion",
        "spiritual_yearning", "hope", "patriotism", "playfulness",
        "loneliness", "acceptance", "wonder", "serenity"
    ];

    var RASAS = [
        "shringara", "karuna", "shanta", "bhakti", "veera",
        "adbhuta", "hasya", "raudra", "bhayanaka", "bibhatsa"
    ];

    var PARJAAY = [
        "Puja", "Prem", "Prakriti", "Swadesh",
        "Anushthanik", "Bichitro", "Nrityanatya"
    ];

    var QUADRANT_LABELS = {
        Q1: "Q1 · happy, excited",
        Q2: "Q2 · tense, agitated",
        Q3: "Q3 · sad, subdued",
        Q4: "Q4 · calm, serene"
    };

    var EMOTION_COLOR = {
        love: "#c98293", joy: "#d8ad55", devotion: "#8c81c8",
        longing: "#7e8fd6", sadness: "#668fc0", serenity: "#69a99e",
        anger: "#c86c69", fear: "#c07a3f", peace: "#69a99e",
        grief: "#668fc0", nostalgia: "#8c81c8", anxiety: "#c07a3f",
        hope: "#d8ad55", patriotism: "#c98293", playfulness: "#d8ad55",
        loneliness: "#668fc0", acceptance: "#69a99e", wonder: "#8c81c8",
        spiritual_yearning: "#8c81c8"
    };

    // A search box only earns its place once the roster is long enough to scroll.
    var SEARCH_FROM = 8;

    var state = {
        useLearned: true,
        experts: [],
        filter: [],
        ranking: [],
        current: null
    };

    var followPicker = null;


    /* =========================
       SMALL HELPERS
    ========================= */

    function el(tag, className, text) {
        var node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    }

    function escapeHTML(value) {
        return String(value === null || value === undefined ? "" : value)
            .replace(/[&<>"]/g, function (character) {
                return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[character];
            });
    }

    function asText(value) {
        if (Array.isArray(value)) return value.join(", ");
        if (value === null || value === undefined) return "";
        return String(value);
    }

    function number(value, fallback) {
        var parsed = Number(value);
        return Number.isFinite(parsed) ? parsed : (fallback || 0);
    }

    function quadrantFrom(valence, arousal) {
        if (valence >= 0) return arousal >= 0 ? "Q1" : "Q4";
        return arousal >= 0 ? "Q2" : "Q3";
    }

    function toast(message) {
        var existing = document.querySelector(".review-toast");
        if (existing) existing.remove();
        var node = el("div", "review-toast", message);
        document.body.appendChild(node);
        setTimeout(function () { node.remove(); }, 2800);
    }

    function rememberedName() {
        try { return window.localStorage.getItem("lyriq-editor") || ""; }
        catch (error) { return ""; }
    }

    function rememberName(name) {
        try { window.localStorage.setItem("lyriq-editor", name); }
        catch (error) { /* private browsing, no matter */ }
    }

    async function api(path, options) {
        var response = await fetch(API + path, options);
        var data = await response.json().catch(function () { return {}; });
        if (!response.ok) throw new Error(data.error || "The request failed.");
        return data;
    }

    function post(path, body) {
        return api(path, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body)
        });
    }


    /* =========================
       FOLLOW PICKER

       Roster on top, selection at the bottom. Any number of experts may be
       ticked; an empty selection means everyone, which is the default. The
       menu stays open while you tick, because choosing five people through
       a menu that closed each time would be miserable.
    ========================= */

    function buildFollowPicker(onChoose) {
        var node = el("div", "expert-filter");
        node.appendChild(el("span", "filter-label", "Follow"));

        var trigger = el("button", "filter-trigger");
        trigger.type = "button";
        trigger.setAttribute("aria-haspopup", "true");
        trigger.setAttribute("aria-expanded", "false");

        var valueText = el("span", "filter-value", "Every expert");
        trigger.appendChild(valueText);
        trigger.appendChild(el("span", "picker-caret", "▾"));

        var menu = el("div", "filter-menu");
        menu.hidden = true;

        // top: the roster
        var head = el("div", "filter-head", "Experts on file");

        var search = document.createElement("input");
        search.type = "text";
        search.className = "filter-search";
        search.placeholder = "Search experts";
        search.hidden = true;

        var list = el("div", "filter-list");

        // bottom: who the model will actually hear from
        var chosenBox = el("div", "filter-chosen");
        var chosenTitle = el("div", "filter-chosen-head");
        var chips = el("div", "filter-chips");
        var clear = el("button", "filter-clear", "Follow everyone");
        clear.type = "button";

        chosenBox.appendChild(chosenTitle);
        chosenBox.appendChild(chips);
        chosenBox.appendChild(clear);

        menu.appendChild(head);
        menu.appendChild(search);
        menu.appendChild(list);
        menu.appendChild(chosenBox);

        var chosen = [];          // empty means every expert

        function close() {
            menu.hidden = true;
            trigger.setAttribute("aria-expanded", "false");
            document.removeEventListener("mousedown", outside, true);
            document.removeEventListener("keydown", escape, true);
        }

        function open() {
            menu.hidden = false;
            trigger.setAttribute("aria-expanded", "true");
            document.addEventListener("mousedown", outside, true);
            document.addEventListener("keydown", escape, true);
            if (!search.hidden) search.focus();
        }

        function outside(event) {
            if (!node.contains(event.target)) close();
        }

        function escape(event) {
            if (event.key !== "Escape") return;
            event.stopPropagation();
            close();
            trigger.focus();
        }

        trigger.addEventListener("click", function () {
            if (menu.hidden) { open(); } else { close(); }
        });

        function summary() {
            if (!chosen.length) {
                return state.experts.length
                    ? "Every expert (" + state.experts.length + ")"
                    : "Every expert";
            }
            if (chosen.length === 1) return chosen[0];
            if (chosen.length === 2) return chosen.join(" and ");
            return chosen.length + " of " + state.experts.length + " experts";
        }

        function toggle(name) {
            var at = chosen.indexOf(name);
            if (at === -1) { chosen.push(name); } else { chosen.splice(at, 1); }
            draw();
            onChoose(chosen.slice());
        }

        function row(expert) {
            var button = el("button", "picker-option filter-option");
            button.type = "button";

            button.appendChild(el("span", "tick-box"));
            button.appendChild(el("span", "filter-name", expert.name));

            var meta = expert.teaching + (expert.teaching === 1 ? " edit" : " edits");
            var standing = state.ranking.indexOf(expert.name);
            if (standing !== -1) meta = "#" + (standing + 1) + " · " + meta;
            button.appendChild(el("span", "filter-meta", meta));

            var on = chosen.indexOf(expert.name) !== -1;
            button.classList.toggle("ticked", on);
            button.setAttribute("aria-pressed", String(on));

            button.addEventListener("click", function () { toggle(expert.name); });
            return button;
        }

        function draw() {
            var needle = search.value.trim().toLowerCase();

            list.innerHTML = "";
            state.experts
                .filter(function (expert) {
                    return !needle || expert.name.toLowerCase().indexOf(needle) !== -1;
                })
                .forEach(function (expert) { list.appendChild(row(expert)); });

            if (!list.children.length) {
                list.appendChild(el("p", "filter-empty",
                    state.experts.length
                        ? "No expert matches that."
                        : "No corrections yet, so nobody to follow."));
            }

            search.hidden = state.experts.length < SEARCH_FROM;
            head.textContent = "Experts on file · " + state.experts.length;

            chips.innerHTML = "";
            if (chosen.length) {
                chosenTitle.textContent = "Following · " + chosen.length;
                chosen.forEach(function (name) {
                    var chip = el("button", "filter-chip");
                    chip.type = "button";
                    chip.title = "Stop following " + name;
                    chip.appendChild(el("span", null, name));
                    chip.appendChild(el("span", "chip-x", "✕"));
                    chip.addEventListener("click", function () { toggle(name); });
                    chips.appendChild(chip);
                });
            } else {
                chosenTitle.textContent = "Following everyone";
                chips.appendChild(el("p", "filter-empty",
                    "Every correction on file guides the model. Tick names above "
                    + "to hear from those experts only."));
            }
            clear.hidden = !chosen.length;

            valueText.textContent = summary();
            trigger.classList.toggle("narrowed", chosen.length > 0);
        }

        search.addEventListener("input", draw);

        clear.addEventListener("click", function () {
            chosen = [];
            draw();
            onChoose([]);
        });

        node.appendChild(trigger);
        node.appendChild(menu);

        return {
            node: node,
            refresh: draw,
            set: function (names) {
                // Keep only names that still exist, so a retired expert does
                // not linger in the filter and silence the model.
                var known = state.experts.map(function (e) { return e.name; });
                chosen = (names || []).filter(function (name) {
                    return known.indexOf(name) !== -1;
                });
                draw();
            }
        };
    }

    function mountControls() {
        var actions = document.querySelector(".actions");
        if (!actions) return;

        var label = el("label", "review-switch");
        var box = document.createElement("input");
        box.type = "checkbox";
        box.id = "use-learned";

        var caption = el("span");
        caption.innerHTML = "Use expert corrections <small id=\"learned-count\"></small>";

        label.appendChild(box);
        label.appendChild(caption);
        actions.appendChild(label);

        followPicker = buildFollowPicker(async function (names) {
            state.filter = names;
            try {
                await post("/preferences", { filter: names });
                toast(names.length === 0
                    ? "Following every expert on file."
                    : names.length === 1
                        ? "Following " + names[0] + " only."
                        : "Following " + names.length + " experts: " + names.join(", ") + ".");
            } catch (error) {
                toast(error.message);
            }
        });
        actions.appendChild(followPicker.node);

        var logButton = el("button", "review-log-open", "Review log");
        logButton.type = "button";
        logButton.addEventListener("click", openLog);
        actions.appendChild(logButton);

        box.addEventListener("change", async function () {
            state.useLearned = box.checked;
            label.classList.toggle("on", box.checked);
            followPicker.node.hidden = !box.checked;
            try {
                await post("/preferences", { use_learned: box.checked });
                toast(box.checked
                    ? "Learned corrections on. They will shape the next reading."
                    : "Learned corrections off. Readings come from the model alone.");
            } catch (error) {
                toast(error.message);
            }
        });

        loadPreferences(box, label);
    }

    function showCount(stats) {
        var teaching = (stats || {}).teaching || 0;
        var count = document.getElementById("learned-count");
        if (!count) return;
        count.textContent = teaching
            ? "· " + teaching + (teaching === 1 ? " edit" : " edits")
            : "· none yet";
    }

    async function loadPreferences(box, label) {
        try {
            var data = await api("/preferences");
            state.useLearned = data.use_learned;
            state.experts = data.experts || [];
            state.filter = data.filter || [];
            state.ranking = data.ranking || [];

            box.checked = data.use_learned;
            label.classList.toggle("on", data.use_learned);
            if (followPicker) {
                followPicker.node.hidden = !data.use_learned;
                followPicker.set(state.filter);
            }
            showCount(data.stats);
        } catch (error) {
            /* the page still works without the review layer */
        }
    }

    async function refreshExperts() {
        try {
            var data = await api("/experts");
            state.experts = data.experts || [];
            state.filter = data.filter || [];
            state.ranking = data.ranking || [];
            if (followPicker) followPicker.set(state.filter);
            showCount(data.stats);
        } catch (error) { /* leave the old list in place */ }
    }


    /* =========================
       PROVENANCE + EDIT BUTTON
    ========================= */

    function describe(learning) {
        if (!learning) return null;

        var experts = learning.experts || [];
        var following = experts.length
            ? " Following " + (experts.length > 3
                ? experts.length + " experts"
                : experts.join(", ")) + "."
            : "";

        if (learning.source === "correction") {
            return {
                tone: "learned",
                text: "Served from a correction of this exact song by "
                    + (learning.editor || "an expert")
                    + ", saved " + (learning.edited_at || "").slice(0, 10)
                    + ". The model was not asked again."
            };
        }
        if (learning.source === "guided") {
            var names = [];
            (learning.matches || []).forEach(function (match) {
                if (names.indexOf(match.editor) === -1) names.push(match.editor);
            });
            var count = (learning.matches || []).length;
            return {
                tone: "learned",
                text: "Read with " + count + " past correction"
                    + (count === 1 ? "" : "s") + " as guidance, from "
                    + names.join(", ") + "."
            };
        }
        return {
            tone: learning.mode === "learned" ? "learned" : "default",
            text: learning.mode === "learned"
                ? "No past correction was close enough to this song, so this is the model's own reading." + following
                : "Default reading. Learned corrections were switched off."
        };
    }

    function attach(data) {
        if (!data || !data.analysis_id) return;

        state.current = data;

        var body = document.getElementById("body");
        if (!body) return;

        var summary = describe(data.learning);
        if (summary) {
            var badge = el("div", "provenance " + summary.tone);
            badge.innerHTML = "<b>" + (summary.tone === "learned" ? "Learned" : "Default")
                + "</b><span>" + escapeHTML(summary.text) + "</span>";

            var matches = (data.learning && data.learning.matches) || [];
            if (matches.length) {
                var why = el("button", "why", "what it leaned on");
                why.type = "button";
                why.addEventListener("click", function () { openMatches(matches); });
                badge.appendChild(why);
            }
            body.appendChild(badge);
        }

        var button = el("button", "secondary edit-output", "Edit this reading");
        button.type = "button";
        button.addEventListener("click", function () { openEditor(data); });
        body.appendChild(button);
    }


    /* =========================
       DRAWER SHELL
    ========================= */

    var drawer = null;

    function closeDrawer() {
        if (!drawer) return;
        drawer.scrim.remove();
        drawer.panel.remove();
        document.removeEventListener("keydown", onEscape);
        drawer = null;
    }

    function onEscape(event) {
        if (event.key === "Escape") closeDrawer();
    }

    function openDrawer(title, blurb) {
        closeDrawer();

        var scrim = el("div", "drawer-scrim");
        scrim.addEventListener("click", closeDrawer);

        var panel = el("aside", "drawer");
        panel.setAttribute("role", "dialog");
        panel.setAttribute("aria-modal", "true");
        panel.setAttribute("aria-label", title);

        var head = el("div", "drawer-head");
        var heading = el("div");
        heading.appendChild(el("div", "result-label", "LYRIQ · Review"));
        heading.appendChild(el("h3", null, title));
        if (blurb) heading.appendChild(el("p", null, blurb));

        var close = el("button", "drawer-close", "✕");
        close.type = "button";
        close.setAttribute("aria-label", "Close");
        close.addEventListener("click", closeDrawer);

        head.appendChild(heading);
        head.appendChild(close);

        var content = el("div", "drawer-body");
        var foot = el("div", "drawer-foot");

        panel.appendChild(head);
        panel.appendChild(content);
        panel.appendChild(foot);

        document.body.appendChild(scrim);
        document.body.appendChild(panel);
        document.addEventListener("keydown", onEscape);

        drawer = { scrim: scrim, panel: panel, body: content, foot: foot };
        return drawer;
    }


    /* =========================
       EDITOR PICKER
    ========================= */

    function ledRow(text) {
        var row = el("button", "mode-row");
        row.type = "button";
        row.appendChild(el("span", "led"));
        row.appendChild(el("span", null, text));
        return row;
    }

    function blink(row) {
        var led = row.querySelector(".led");
        led.classList.remove("blink");
        void led.offsetWidth;
        led.classList.add("blink");
    }

    function buildPicker(list, input, options) {
        var node = el("div", "picker");

        var trigger = el("button", "picker-trigger");
        trigger.type = "button";
        trigger.setAttribute("aria-haspopup", "true");
        trigger.setAttribute("aria-expanded", "false");

        var swatch = el("span", "swatch");
        var valueText = el("span", "picker-value");
        var caret = el("span", "picker-caret", "▾");
        trigger.appendChild(swatch);
        trigger.appendChild(valueText);
        trigger.appendChild(caret);

        var freeWrap = el("div", "free-wrap");
        freeWrap.hidden = true;
        var back = el("button", "picker-back", "▾");
        back.type = "button";
        back.title = "Back to the list";
        freeWrap.appendChild(input);
        freeWrap.appendChild(back);

        var menu = el("div", "picker-menu");
        menu.hidden = true;

        var listWrap = el("div", "picker-list");
        listWrap.hidden = true;

        if (!options.strict) {
            var modes = el("div", "picker-modes");
            var listRow = ledRow("Choose from the list");
            var freeRow = ledRow("Type manually");
            modes.appendChild(listRow);
            modes.appendChild(freeRow);
            menu.appendChild(modes);

            listRow.addEventListener("click", function () {
                blink(listRow);
                listRow.classList.add("on");
                freeRow.classList.remove("on");
                listWrap.hidden = false;
            });

            freeRow.addEventListener("click", function () {
                blink(freeRow);
                freeRow.classList.add("on");
                listRow.classList.remove("on");
                setTimeout(function () {
                    close();
                    trigger.hidden = true;
                    freeWrap.hidden = false;
                    input.focus();
                    input.select();
                }, 280);
            });
        }

        menu.appendChild(listWrap);

        list.forEach(function (option) {
            if (!option) return;
            var row = el("button", "picker-option");
            row.type = "button";
            row.dataset.value = option;

            var colour = options.swatch ? EMOTION_COLOR[option.toLowerCase()] : null;
            if (colour) {
                var dot = el("span", "swatch");
                dot.style.background = colour;
                row.appendChild(dot);
            }
            row.appendChild(el("span", null, option));

            row.addEventListener("click", function () {
                input.value = option;
                input.dispatchEvent(new Event("change"));
                refresh();
                close();
            });
            listWrap.appendChild(row);
        });

        back.addEventListener("click", function () {
            freeWrap.hidden = true;
            trigger.hidden = false;
            refresh();
            open();
        });

        function refresh() {
            var current = asText(input.value);
            valueText.textContent = current || "Not specified";
            valueText.classList.toggle("empty", !current);

            var colour = options.swatch ? EMOTION_COLOR[current.toLowerCase()] : null;
            swatch.style.background = colour || "transparent";
            swatch.hidden = !colour;

            [].forEach.call(listWrap.children, function (row) {
                row.classList.toggle("on", row.dataset.value === current);
            });
        }

        function open() {
            var room = window.innerHeight - trigger.getBoundingClientRect().bottom;
            node.classList.toggle("up", room < 300);
            listWrap.hidden = !options.strict;
            menu.hidden = false;
            trigger.setAttribute("aria-expanded", "true");
            document.addEventListener("mousedown", onOutside, true);
            document.addEventListener("keydown", onKey, true);
        }

        function close() {
            menu.hidden = true;
            trigger.setAttribute("aria-expanded", "false");
            document.removeEventListener("mousedown", onOutside, true);
            document.removeEventListener("keydown", onKey, true);
        }

        function onOutside(event) {
            if (!node.contains(event.target)) close();
        }

        function onKey(event) {
            if (event.key !== "Escape") return;
            event.stopPropagation();
            close();
            trigger.focus();
        }

        trigger.addEventListener("click", function () {
            if (menu.hidden) { open(); } else { close(); }
        });

        node.appendChild(trigger);
        node.appendChild(freeWrap);
        node.appendChild(menu);

        input._refresh = refresh;
        refresh();

        return { node: node, refresh: refresh };
    }


    /* =========================
       FORM BUILDERS
    ========================= */

    function group(parent, title) {
        parent.appendChild(el("div", "group-title", title));
    }

    function textField(parent, id, label, value, options) {
        options = options || {};

        var wrap = el("div", "field");
        var labelNode = el("label", null, label);
        labelNode.setAttribute("for", id);
        if (options.required) {
            labelNode.appendChild(el("span", "required", " · required"));
        }
        wrap.appendChild(labelNode);

        if (!options.list) {
            var plain = options.multiline
                ? document.createElement("textarea")
                : document.createElement("input");
            if (options.multiline) { plain.rows = options.rows || 4; }
            else { plain.type = "text"; }

            plain.id = id;
            plain.value = asText(value);
            if (options.placeholder) plain.placeholder = options.placeholder;
            wrap.appendChild(plain);

            if (options.hint) wrap.appendChild(el("span", "was", options.hint));
            if (options.was !== undefined && asText(options.was) !== "") {
                var wasNote = el("span", "was");
                wasNote.innerHTML = "model said <b>" + escapeHTML(asText(options.was)) + "</b>";
                wrap.appendChild(wasNote);
            }
            parent.appendChild(wrap);
            return plain;
        }

        var input = document.createElement("input");
        input.type = "text";
        input.id = id;
        input.className = "free-input";
        input.value = asText(value);
        input.placeholder = options.placeholder || "Type anything, e.g. playfulness / love";

        var picker = buildPicker(options.list, input, options);
        wrap.appendChild(picker.node);

        if (options.hint) wrap.appendChild(el("span", "was", options.hint));
        if (options.was !== undefined && asText(options.was) !== "") {
            var was = el("span", "was");
            was.innerHTML = "model said <b>" + escapeHTML(asText(options.was)) + "</b>";
            wrap.appendChild(was);
        }

        parent.appendChild(wrap);
        return input;
    }

    function sliderField(parent, id, label, value, low, high, was) {
        var wrap = el("div", "field");
        wrap.appendChild(el("label", null, label)).setAttribute("for", id);

        var row = el("div", "slider-row");
        var input = document.createElement("input");
        input.type = "range";
        input.id = id;
        input.min = low;
        input.max = high;
        input.step = 0.01;
        input.value = number(value);

        var readout = document.createElement("output");
        readout.textContent = number(value).toFixed(2);

        input.addEventListener("input", function () {
            readout.textContent = number(input.value).toFixed(2);
            syncQuadrant();
        });

        row.appendChild(input);
        row.appendChild(readout);
        wrap.appendChild(row);

        if (was !== undefined && was !== null) {
            var note = el("span", "was");
            note.innerHTML = "model said <b>" + number(was).toFixed(2) + "</b>";
            wrap.appendChild(note);
        }
        parent.appendChild(wrap);
        return input;
    }

    function syncQuadrant() {
        var valence = document.getElementById("edit-valence");
        var arousal = document.getElementById("edit-arousal");
        var quadrant = document.getElementById("edit-quadrant");
        if (!valence || !arousal || !quadrant) return;
        if (quadrant.dataset.touched === "1") return;
        quadrant.value = quadrantFrom(number(valence.value), number(arousal.value));
        if (quadrant._refresh) quadrant._refresh();
    }


    /* =========================
       EDITOR
    ========================= */

    function openEditor(data) {
        var shell = openDrawer(
            "Correct this reading",
            "Change what the model got wrong, then say who you are and why. The "
            + "reasoning is what teaches the next reading; a changed number on its "
            + "own teaches very little."
        );

        var body = shell.body;

        var errorSlot = el("div");
        body.appendChild(errorSlot);

        group(body, "Verdict");

        var primary = textField(body, "edit-primary", "Primary emotion",
            data.primary_emotion, {
                list: LABELS.slice(),
                swatch: true,
                hint: "your own words, joined with / when one will not do"
            });

        var canonical = textField(body, "edit-canonical", "Canonical emotion",
            data.canonical_emotion, {
                list: LABELS.slice(),
                swatch: true,
                hint: "the same reading in closed-set labels, so readings stay countable"
            });

        var secondary = textField(body, "edit-secondary",
            "Secondary emotions, comma separated", data.secondary_emotions,
            { placeholder: "longing, devotion" });

        var mixedWrap = el("div", "field");
        var mixedLabel = el("label", "check-row");
        var mixed = document.createElement("input");
        mixed.type = "checkbox";
        mixed.id = "edit-mixed";
        mixed.checked = Boolean(data.mixed_emotion);
        mixedLabel.appendChild(mixed);
        mixedLabel.appendChild(el("span", null, "Mixed expression, sorrow and serenity together"));
        mixedWrap.appendChild(mixedLabel);
        body.appendChild(mixedWrap);

        group(body, "Circumplex");

        var valence = sliderField(body, "edit-valence", "Valence",
            data.valence, -1, 1, data.valence);
        var arousal = sliderField(body, "edit-arousal", "Arousal",
            data.arousal, -1, 1, data.arousal);

        var quadrant = textField(body, "edit-quadrant", "Quadrant", data.quadrant, {
            list: ["Q1", "Q2", "Q3", "Q4"],
            strict: true,
            hint: "follows valence and arousal until you choose one yourself"
        });
        quadrant.addEventListener("change", function () {
            quadrant.dataset.touched = "1";
        });

        var confidence = sliderField(body, "edit-confidence", "Confidence",
            data.confidence, 0, 1, data.confidence);

        group(body, "Cultural reading");

        var rasa = textField(body, "edit-rasa", "Rasa", data.rasa,
            { list: RASAS.slice(), placeholder: "Type a rasa, or a compound one" });

        var parjaay = textField(body, "edit-parjaay", "Parjaay", data.parjaay,
            { list: PARJAAY.slice(), placeholder: "Type a parjaay" });

        var pair = el("div", "field-pair");
        body.appendChild(pair);
        var tradition = textField(pair, "edit-tradition", "Tradition", data.tradition, {});
        var language = textField(pair, "edit-language", "Language", data.language, {});

        group(body, "Prose");

        var summary = textField(body, "edit-summary", "The reading", data.summary,
            { multiline: true, rows: 6 });

        var therapy = textField(body, "edit-therapy", "Music therapy use",
            data.music_therapy_context || data.music_therapy, {});

        var tags = textField(body, "edit-tags", "Recommendation tags, comma separated",
            data.recommendation_tags, { placeholder: "late-night, devotional" });

        group(body, "Who and why");

        var editor = textField(body, "edit-editor", "Expert", rememberedName(), {
            required: true,
            placeholder: "Your name",
            hint: "corrections are attributed, so a reading can be asked to follow you"
        });

        var note = textField(body, "edit-note", "Why the model was wrong", "", {
            multiline: true,
            rows: 4,
            placeholder: "e.g. biraha in Baul is separation from the divine, "
                + "not plain sadness, so valence should not sit this low."
        });

        var save = el("button", "primary", "Save correction");
        save.type = "button";

        var cancel = el("button", "secondary", "Cancel");
        cancel.type = "button";
        cancel.addEventListener("click", closeDrawer);

        shell.foot.appendChild(save);
        shell.foot.appendChild(cancel);

        save.addEventListener("click", async function () {
            errorSlot.innerHTML = "";

            if (!editor.value.trim()) {
                errorSlot.appendChild(el("div", "drawer-error",
                    "Put your name to this correction first. It decides whose "
                    + "judgement the model can be asked to follow later."));
                editor.focus();
                return;
            }

            save.disabled = true;
            save.textContent = "Saving";

            var corrected = {
                primary_emotion: primary.value.trim(),
                canonical_emotion: canonical.value.trim(),
                secondary_emotions: secondary.value,
                mixed_emotion: mixed.checked,
                valence: number(valence.value),
                arousal: number(arousal.value),
                quadrant: quadrant.value,
                confidence: number(confidence.value),
                rasa: rasa.value.trim() || null,
                parjaay: parjaay.value.trim() || null,
                tradition: tradition.value.trim() || null,
                language: language.value.trim() || null,
                summary: summary.value.trim(),
                music_therapy: therapy.value.trim(),
                music_therapy_context: therapy.value.trim(),
                recommendation_tags: tags.value
            };

            try {
                var result = await post("/correction", {
                    analysis_id: data.analysis_id,
                    corrected: corrected,
                    editor: editor.value,
                    note: note.value
                });

                rememberName(editor.value.trim());
                closeDrawer();
                repaint(result.corrected, data, result);
                refreshExperts();
                toast(result.embedded
                    ? "Saved under " + result.editor + ". It will guide similar songs now."
                    : "Saved under " + result.editor + ". Embeddings were unavailable, "
                    + "so it applies to this exact song only.");
            } catch (error) {
                errorSlot.appendChild(el("div", "drawer-error", error.message));
                save.disabled = false;
                save.textContent = "Save correction";
            }
        });
    }

    function repaint(corrected, previous, result) {
        var merged = Object.assign({}, previous, corrected);
        merged.analysis_id = previous.analysis_id;
        merged.quadrant_label = QUADRANT_LABELS[merged.quadrant] || merged.quadrant;
        merged.learning = {
            mode: "learned",
            source: "correction",
            correction_id: result.correction_id,
            edited_at: new Date().toISOString(),
            editor: result.editor,
            experts: [],
            matches: []
        };
        if (typeof window.renderResult === "function") {
            window.renderResult(merged);
        }
    }


    /* =========================
       WHAT IT LEANED ON
    ========================= */

    function openMatches(matches) {
        var shell = openDrawer(
            "What shaped this reading",
            "Past corrections the analyser was shown before reading this song, "
            + "in the order it was told to weigh them."
        );

        matches.forEach(function (match) {
            var row = el("div", "log-row");

            var top = el("div", "log-top");
            top.appendChild(el("p", "log-excerpt", match.excerpt));
            top.appendChild(el("span", "log-meta", "similarity " + match.similarity));
            row.appendChild(top);

            var by = el("div", "log-by");
            by.appendChild(el("span", "editor-chip", match.editor));
            row.appendChild(by);

            var fields = el("div", "log-fields");
            (match.fields || []).forEach(function (field) {
                fields.appendChild(el("span", "pill", field.replace(/_/g, " ")));
            });
            row.appendChild(fields);

            if (match.note) row.appendChild(el("p", "log-note", match.note));
            shell.body.appendChild(row);
        });

        var done = el("button", "secondary", "Close");
        done.type = "button";
        done.addEventListener("click", closeDrawer);
        shell.foot.appendChild(done);
    }


    /* =========================
       EXPERT STANDINGS
    ========================= */

    function rankingBlock(experts, ranking) {
        var wrap = el("div", "standings");
        wrap.appendChild(el("div", "group-title", "Expert standings"));

        wrap.appendChild(el("p", "log-note",
            "Rank your top three. When several corrections match the same song, "
            + "the model is told to prefer the higher-ranked expert."));

        var chosen = [ranking[0] || "", ranking[1] || "", ranking[2] || ""];
        var pickers = [];

        chosen.forEach(function (_, index) {
            var row = el("div", "rank-row");
            row.appendChild(el("span", "rank-place", "#" + (index + 1)));

            var picker = el("div", "rank-picker");

            var trigger = el("button", "filter-trigger rank-trigger");
            trigger.type = "button";
            trigger.setAttribute("aria-expanded", "false");
            var valueText = el("span", "filter-value");
            trigger.appendChild(valueText);
            trigger.appendChild(el("span", "picker-caret", "▾"));

            var menu = el("div", "filter-menu rank-menu");
            menu.hidden = true;

            function close() {
                menu.hidden = true;
                trigger.setAttribute("aria-expanded", "false");
                document.removeEventListener("mousedown", outside, true);
            }

            function outside(event) {
                if (!picker.contains(event.target)) close();
            }

            trigger.addEventListener("click", function () {
                if (menu.hidden) {
                    menu.hidden = false;
                    trigger.setAttribute("aria-expanded", "true");
                    document.addEventListener("mousedown", outside, true);
                } else {
                    close();
                }
            });

            function label(name) {
                if (!name) return "Nobody";
                var expert = experts.filter(function (e) { return e.name === name; })[0];
                return expert
                    ? name + " · " + expert.teaching
                        + (expert.teaching === 1 ? " edit" : " edits")
                    : name;
            }

            function draw() {
                menu.innerHTML = "";
                [""].concat(experts.map(function (e) { return e.name; }))
                    .forEach(function (name) {
                        var option = el("button", "picker-option");
                        option.type = "button";
                        option.textContent = label(name);
                        option.classList.toggle("on", name === chosen[index]);
                        option.addEventListener("click", function () {
                            chosen[index] = name;
                            valueText.textContent = label(name);
                            close();
                            pickers.forEach(function (p) { p.draw(); });
                        });
                        menu.appendChild(option);
                    });
                valueText.textContent = label(chosen[index]);
            }

            picker.appendChild(trigger);
            picker.appendChild(menu);
            row.appendChild(picker);
            wrap.appendChild(row);

            pickers.push({ draw: draw });
            draw();
        });

        var save = el("button", "secondary rank-save", "Save standings");
        save.type = "button";
        save.addEventListener("click", async function () {
            var order = [];
            chosen.forEach(function (name) {
                if (name && order.indexOf(name) === -1) order.push(name);
            });
            try {
                var result = await post("/experts/ranking", { ranking: order });
                state.ranking = result.ranking || [];
                if (followPicker) followPicker.refresh();
                toast(state.ranking.length
                    ? "Standings saved: " + state.ranking.join(" then ") + "."
                    : "Standings cleared. Every expert is weighed equally.");
            } catch (error) {
                toast(error.message);
            }
        });
        wrap.appendChild(save);

        return wrap;
    }


    /* =========================
       REVIEW LOG
    ========================= */

    async function openLog() {
        var shell = openDrawer(
            "Review log",
            "Who changed what, and whose judgement is still teaching. Retiring a "
            + "correction keeps the record but stops it shaping later readings."
        );

        shell.body.appendChild(el("p", "log-empty", "Loading…"));

        try {
            var data = await api("/corrections?limit=100");
            shell.body.innerHTML = "";

            state.experts = data.experts || [];
            state.ranking = data.ranking || [];

            var stats = data.stats || {};
            var summary = el("p", "drawer-note");
            summary.textContent = stats.corrections
                ? stats.corrections + " correction"
                    + (stats.corrections === 1 ? "" : "s") + " on file, "
                    + stats.teaching + " still teaching, across "
                    + stats.analyses + " readings."
                : "No corrections yet. Read a song, then edit what the model got wrong.";
            shell.body.appendChild(summary);

            if (state.experts.length) {
                shell.body.appendChild(rankingBlock(state.experts, state.ranking));
                shell.body.appendChild(el("div", "group-title", "Every correction"));
            }

            (data.corrections || []).forEach(function (correction) {
                shell.body.appendChild(logRow(correction));
            });

            var people = stats.experts || state.experts.length;
            var tally = el("div", "log-tally");
            tally.innerHTML = "<b>" + people + "</b> "
                + (people === 1 ? "person has" : "different people have")
                + " edited readings here"
                + (state.experts.length
                    ? ": " + escapeHTML(state.experts.map(function (e) {
                        return e.name + " (" + e.corrections + ")";
                    }).join(", "))
                    : "") + ".";
            shell.body.appendChild(tally);

            if (followPicker) followPicker.refresh();
        } catch (error) {
            shell.body.innerHTML = "";
            shell.body.appendChild(el("div", "drawer-error", error.message));
        }

        var done = el("button", "secondary", "Close");
        done.type = "button";
        done.addEventListener("click", closeDrawer);
        shell.foot.appendChild(done);
    }

    function logRow(correction) {
        var row = el("div", "log-row" + (correction.active ? "" : " retired"));

        var top = el("div", "log-top");
        top.appendChild(el("p", "log-excerpt", correction.excerpt));
        top.appendChild(el("span", "log-meta", (correction.created_at || "").slice(0, 10)));
        row.appendChild(top);

        var by = el("div", "log-by");
        var chip = el("span", "editor-chip", correction.editor);
        var standing = state.ranking.indexOf(correction.editor);
        if (standing !== -1) {
            chip.classList.add("ranked");
            chip.appendChild(el("span", "rank-badge", "#" + (standing + 1)));
        }
        by.appendChild(chip);
        if (!correction.active) by.appendChild(el("span", "log-meta", "retired"));
        row.appendChild(by);

        var fields = el("div", "log-fields");
        Object.keys(correction.changed || {}).forEach(function (field) {
            var move = correction.changed[field];
            fields.appendChild(el("span", "pill",
                field.replace(/_/g, " ") + " → " + (move.to || "cleared").slice(0, 28)));
        });
        row.appendChild(fields);

        if (correction.note) row.appendChild(el("p", "log-note", correction.note));

        var actions = el("div", "log-actions");
        var toggle = el("button", "log-toggle",
            correction.active ? "Retire this correction" : "Put it back to work");
        toggle.type = "button";
        toggle.addEventListener("click", async function () {
            try {
                var result = await post("/correction/" + correction.id + "/retire",
                    { restore: !correction.active });
                correction.active = result.active ? 1 : 0;
                row.classList.toggle("retired", !result.active);
                toggle.textContent = result.active
                    ? "Retire this correction"
                    : "Put it back to work";
                refreshExperts();
                toast(result.active ? "Teaching again." : "Retired. It stops teaching now.");
            } catch (error) {
                toast(error.message);
            }
        });
        actions.appendChild(toggle);
        row.appendChild(actions);

        return row;
    }


    /* =========================
       MOUNT
    ========================= */

    function start() {
        mountControls();

        // renderResult is a top-level function declaration in the page's own
        // script, so it lives on the global object and can be wrapped here.
        var original = window.renderResult;
        if (typeof original === "function") {
            window.renderResult = function (data) {
                original(data);
                attach(data);
            };
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", start);
    } else {
        start();
    }
})();