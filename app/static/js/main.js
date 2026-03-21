'use strict';

// ═══════════════════════════════════════════════════════════════
//  H&G Money Transfer — main.js
//  Theme toggle • Counter animation • Card FX • Page transitions
// ═══════════════════════════════════════════════════════════════


// ── THEME ───────────────────────────────────────────────────────
// The anti-flash <script> at the top of <body> in layout.html sets
// the initial class before anything is rendered. This module just
// wires up toggle buttons and keeps localStorage in sync.

function applyTheme(dark) {
    document.body.classList.toggle('dark-mode', dark);
    document.body.classList.toggle('light-mode', !dark);
    document.querySelectorAll('.theme-icon').forEach(function (el) {
        el.textContent = dark ? '\u2600\uFE0F' : '\uD83C\uDF19'; // ☀️ : 🌙
    });
    localStorage.setItem('theme', dark ? 'dark' : 'light');
}

function initTheme() {
    // Sync icon(s) with the class already applied by the anti-flash script
    var isDark = document.body.classList.contains('dark-mode');
    document.querySelectorAll('.theme-icon').forEach(function (el) {
        el.textContent = isDark ? '\u2600\uFE0F' : '\uD83C\uDF19';
    });

    document.querySelectorAll('.theme-toggle-btn').forEach(function (btn) {
        btn.addEventListener('click', function () {
            applyTheme(!document.body.classList.contains('dark-mode'));
        });
    });
}


// ── COUNTER ANIMATION ───────────────────────────────────────────
// Reads the rendered number + suffix from .stat-value elements and
// counts up from 0 on page load. Skips zero values.

function animateCounter(el) {
    var fullText = (el.innerText || '').trim();
    // Match a number (with optional thousands commas and decimals) then suffix
    var match = fullText.match(/^([\d,]+(?:\.\d+)?)(\s*.*)$/);
    if (!match) return;

    var numStr  = match[1].replace(/,/g, '');
    var suffix  = match[2] || '';
    var target  = parseFloat(numStr);
    if (isNaN(target) || target === 0) return;

    var decimals = match[1].indexOf('.') !== -1
        ? match[1].split('.')[1].length
        : 0;
    var duration = 900;
    var start    = performance.now();

    function tick(now) {
        var p     = Math.min((now - start) / duration, 1);
        var eased = 1 - Math.pow(1 - p, 3);          // ease-out cubic
        el.innerText = (target * eased).toLocaleString(undefined, {
            minimumFractionDigits: decimals,
            maximumFractionDigits: decimals
        }) + suffix;
        if (p < 1) requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
}

function initCounters() {
    document.querySelectorAll('.stat-value').forEach(function (el, i) {
        // Stagger each counter slightly so they don't all start at once
        setTimeout(function () { animateCounter(el); }, 200 + i * 80);
    });
}


// ── CARD APPEAR ANIMATIONS ──────────────────────────────────────
function initCardAnimations() {
    document.querySelectorAll('.card').forEach(function (card, i) {
        card.style.animationDelay = (i * 0.06) + 's';
        card.classList.add('card-appear');
    });
}


// ── SIDEBAR RIPPLE ──────────────────────────────────────────────
function initSidebarRipple() {
    document.querySelectorAll('.sidebar-menu a').forEach(function (link) {
        link.addEventListener('click', function () {
            var ripple = document.createElement('span');
            ripple.classList.add('ripple');
            this.appendChild(ripple);
            var self = this;
            setTimeout(function () {
                if (ripple.parentNode === self) self.removeChild(ripple);
            }, 600);
        });
    });
}


// ── COLLAPSIBLE SECTIONS ────────────────────────────────────────
function initCollapsibles() {
    document.querySelectorAll('.collapsible-header').forEach(function (header) {
        header.addEventListener('click', function (e) {
            if (e.target.closest('a, button')) return;
            var body = this.nextElementSibling;
            if (!body || !body.classList.contains('collapsible-body')) return;
            this.classList.toggle('collapsed');
            body.classList.toggle('collapsed');
        });
    });
}


// ── 3D TILT (mouse-follow) ──────────────────────────────────────
// Pure CSS can only do a static tilt; mouse-follow requires JS.
function initTilt() {
    document.querySelectorAll('.stat-card').forEach(function (card) {
        card.addEventListener('mousemove', function (e) {
            var rect = card.getBoundingClientRect();
            var x = (e.clientX - rect.left) / rect.width  - 0.5; // -0.5 … 0.5
            var y = (e.clientY - rect.top)  / rect.height - 0.5;
            card.style.transform =
                'perspective(1000px) rotateX(' + (-y * 7) + 'deg) rotateY(' + (x * 7) + 'deg) translateY(-4px)';
            card.style.boxShadow =
                '0 12px 32px rgba(0,0,0,0.18), ' +
                (-x * 10) + 'px ' + (-y * 10) + 'px 22px rgba(91,158,185,0.18)';
        });
        card.addEventListener('mouseleave', function () {
            card.style.transform  = '';
            card.style.boxShadow  = '';
        });
    });
}


// ── PAGE TRANSITIONS ────────────────────────────────────────────
// Fades out .main-content on sidebar / bottom-nav link clicks,
// then navigates after the animation completes (~180ms).
function initPageTransitions() {
    document.querySelectorAll('.sidebar-menu a, .bottom-nav-item').forEach(function (link) {
        var href = link.getAttribute('href');
        if (!href || href === '#') return;
        link.addEventListener('click', function (e) {
            e.preventDefault();
            document.body.classList.add('page-exiting');
            var dest = href;
            setTimeout(function () { window.location.href = dest; }, 180);
        });
    });
}


// ── INIT ────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', function () {
    initTheme();
    initCounters();
    initCardAnimations();
    initSidebarRipple();
    initCollapsibles();
    initTilt();
    initPageTransitions();
});
