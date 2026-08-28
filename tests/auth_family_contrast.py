"""Shared public-auth contrast contracts for Playwright.

Login/register are a light form. Reset/invite use a dark card. Both sit on
body.crm-auth-page and inherit html[data-theme]. These helpers fail when
dark-theme crm-input tokens, near-white type, or leftover t-input orange
slabs win on those screens.

Do not import this from pytest unit tests. CI unit does not install a
Playwright browser. run_tests.py is the browser job.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from models import OrganizationInvite, User, db


AUTH_FAMILY_THEMES = ("dark", "light")

_LIGHT_COPY = (
    ".title",
    ".subtitle",
    ".kicker",
    ".label",
    ".hint-link",
    ".footer-link",
    ".support-row",
    ".support-action",
    ".legal",
    ".text-slate-600",
)

_DARK_COPY = (
    ".auth-title",
    ".auth-subtitle",
    ".auth-kicker",
    ".auth-label",
    ".auth-back",
)


def auth_family_page_specs(reset_path: str, invite_path: str) -> list[dict]:
    """Return the five public auth screens. Caller supplies live token URLs."""
    return [
        {
            "name": "login",
            "path": "/login",
            "form_kind": "light",
            "scope": ".login-page",
            "expect_text": ("Sign in", "Forgot password?"),
            "copy": list(_LIGHT_COPY),
            "fill": (
                ("input[name='username']", "agent@example.com"),
                ("input[name='password']", "not-a-real-password"),
            ),
        },
        {
            "name": "register",
            "path": "/register",
            "form_kind": "light",
            "scope": ".register-page",
            "expect_text": (
                "Create your account.",
                "More on what's included is on",
                "the free real estate CRM page",
            ),
            "copy": list(_LIGHT_COPY),
            "fill": (
                ("input[name='email']", "new-agent@example.com"),
                ("input[name='password']", "not-a-real-password"),
            ),
        },
        {
            "name": "forgot-password",
            "path": "/reset_password",
            "form_kind": "dark",
            "scope": ".auth-card",
            "expect_text": ("Forgot Password?",),
            "copy": list(_DARK_COPY),
            "fill": (("input[name='email']", "agent@example.com"),),
        },
        {
            "name": "reset-password",
            "path": reset_path,
            "form_kind": "dark",
            "scope": ".auth-card",
            "expect_text": ("Create New Password",),
            "copy": list(_DARK_COPY),
            "fill": (
                ("input[name='password']", "not-a-real-password"),
                ("input[name='confirm_password']", "not-a-real-password"),
            ),
        },
        {
            "name": "accept-invite",
            "path": invite_path,
            "form_kind": "dark",
            "scope": ".auth-card",
            "expect_text": ("You're invited to join",),
            "copy": list(_DARK_COPY),
            "fill": (
                ("input[name='username']", "invitee_browser"),
                ("input[name='first_name']", "Pat"),
            ),
        },
    ]


def seed_auth_family_urls(username: str) -> dict[str, str]:
    """Mint a live reset token and invite against the browser-test database."""
    from app import create_app

    app = create_app()
    with app.app_context():
        user = (
            User.query.filter_by(email=username).first()
            or User.query.filter_by(username=username).first()
        )
        if user is None:
            raise RuntimeError(
                f"Browser test user {username} is missing. "
                "Run tests/bootstrap_browser_test_db.py first."
            )
        reset_token = user.get_reset_token()
        invite_token = OrganizationInvite.generate_token()
        db.session.add(
            OrganizationInvite(
                organization_id=user.organization_id,
                email="auth-family-invitee@example.com",
                invited_by_id=user.id,
                role="agent",
                token=invite_token,
                expires_at=datetime.utcnow() + timedelta(hours=24),
            )
        )
        db.session.commit()
    return {
        "reset": f"/reset_password/{reset_token}",
        "invite": f"/invite/{invite_token}",
    }


def assert_auth_family_report(report: dict, page_name: str, theme: str) -> None:
    if report.get("error"):
        raise AssertionError(f"{page_name} theme={theme}: {report['error']}")
    if report.get("theme") != theme:
        raise AssertionError(
            f"{page_name}: html[data-theme] is {report.get('theme')!r}, "
            f"expected {theme!r}"
        )
    failures = report.get("failures") or []
    if failures:
        raise AssertionError(f"{page_name} theme={theme}: " + "; ".join(failures))


COLLECT_AUTH_FAMILY_REPORT = r"""
(spec) => {
  const failures = [];
  const html = document.documentElement;
  const theme = html.dataset.theme || "";
  const formKind = spec.form_kind;
  const scope = document.querySelector(spec.scope);
  if (!document.body.classList.contains("crm-auth-page")) {
    return { error: "body.crm-auth-page missing", theme, failures };
  }
  if (!scope) {
    return { error: `scope ${spec.scope} missing`, theme, failures };
  }

  const parse = (value) => {
    if (!value || value === "transparent" || value === "none") return null;
    const m = String(value).match(
      /rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)(?:\s*,\s*([\d.]+))?\s*\)/i
    );
    if (!m) return null;
    return {
      r: Number(m[1]),
      g: Number(m[2]),
      b: Number(m[3]),
      a: m[4] === undefined ? 1 : Number(m[4]),
    };
  };

  const lin = (c) => {
    const n = c / 255;
    return n <= 0.04045 ? n / 12.92 : Math.pow((n + 0.055) / 1.055, 2.4);
  };

  const lum = (c) => {
    if (!c) return null;
    return 0.2126 * lin(c.r) + 0.7152 * lin(c.g) + 0.0722 * lin(c.b);
  };

  const composite = (fg, bg) => {
    if (!fg) return bg;
    const a = Math.max(0, Math.min(1, fg.a));
    const base = bg || { r: 255, g: 255, b: 255, a: 1 };
    return {
      r: fg.r * a + base.r * (1 - a),
      g: fg.g * a + base.g * (1 - a),
      b: fg.b * a + base.b * (1 - a),
      a: 1,
    };
  };

  const contrast = (a, b) => {
    const la = lum(a);
    const lb = lum(b);
    if (la == null || lb == null) return null;
    const hi = Math.max(la, lb);
    const lo = Math.min(la, lb);
    return (hi + 0.05) / (lo + 0.05);
  };

  const near = (a, b, tol) => {
    if (!a || !b) return false;
    return (
      Math.abs(a.r - b.r) <= tol &&
      Math.abs(a.g - b.g) <= tol &&
      Math.abs(a.b - b.b) <= tol
    );
  };

  const isOrange = (c) => {
    if (!c || c.a < 0.06) return false;
    const span = Math.max(c.r, c.g, c.b) - Math.min(c.r, c.g, c.b);
    if (span < 28) return false;
    return c.r > 180 && c.g > 40 && c.g < 210 && c.b < 140 && c.r - c.b > 50;
  };

  const orangeIn = (value) => {
    if (!value || value === "none" || value === "transparent") return false;
    const re = /rgba?\(([^)]+)\)/gi;
    let match;
    while ((match = re.exec(String(value)))) {
      const parts = match[1].split(",").map((s) => s.trim());
      const color = {
        r: Number(parts[0]),
        g: Number(parts[1]),
        b: Number(parts[2]),
        a: parts[3] === undefined ? 1 : Number(parts[3]),
      };
      if (isOrange(color)) return true;
    }
    return false;
  };

  const visibleTextColor = (style) => {
    const fill = parse(style.webkitTextFillColor);
    if (fill && fill.a > 0.02) return fill;
    return parse(style.color);
  };

  const probe = document.createElement("div");
  probe.style.color = "var(--ink)";
  probe.style.backgroundColor = "var(--paper)";
  probe.style.position = "absolute";
  probe.style.left = "-9999px";
  document.body.appendChild(probe);
  const ink = parse(getComputedStyle(probe).color);
  const paper = parse(getComputedStyle(probe).backgroundColor);
  probe.remove();
  const inkLum = lum(ink);

  const skipInput = (el) => {
    const type = (el.type || "").toLowerCase();
    if (["hidden", "checkbox", "radio", "submit", "button", "file"].includes(type)) {
      return true;
    }
    if (el.name === "referral_code") return true;
    if (el.closest(".left")) return true;
    const box = el.getBoundingClientRect();
    return box.width < 2 || box.height < 2;
  };

  const skipCopy = (el) => {
    if (!el || el.closest(".left")) return true;
    if (el.closest(".btn, .auth-btn, button[type='submit']")) return true;
    if (el.closest("#passwordStrengthBar")) return true;
    const box = el.getBoundingClientRect();
    return box.width < 2 || box.height < 2;
  };

  const inputs = [...scope.querySelectorAll("input")].filter((el) => !skipInput(el));
  if (!inputs.length) {
    failures.push("no visible inputs in scope");
  }

  for (const el of inputs) {
    const cls = el.className || "";
    const label = `${el.name || el.id || el.type} (${cls})`;
    if (el.classList.contains("crm-input") || el.classList.contains("t-input")) {
      failures.push(`${label} still uses crm-input/t-input`);
    }
    if (el.closest(".t-input-wrap")) {
      failures.push(`${label} is still inside t-input-wrap`);
    }

    const style = getComputedStyle(el);
    const bg = parse(style.backgroundColor) || { r: 255, g: 255, b: 255, a: 1 };
    const fg = visibleTextColor(style);
    const phStyle = getComputedStyle(el, "::placeholder");
    const ph = visibleTextColor(phStyle);
    const solidBg = composite(bg, { r: 255, g: 255, b: 255, a: 1 });
    const textOnBg = composite(fg, solidBg);
    const phOnBg = composite(ph, solidBg);
    const bgLum = lum(solidBg);
    const textLum = lum(textOnBg);
    const phLum = lum(phOnBg);
    const textContrast = contrast(textOnBg, solidBg);
    const phContrast = contrast(phOnBg, solidBg);

    if (document.activeElement === el) {
      failures.push(`${label} was focused while measuring rest state`);
    }
    if (orangeIn(style.boxShadow)) {
      failures.push(`${label} rest box-shadow still has an orange halo (${style.boxShadow})`);
    }
    if (orangeIn(style.backgroundColor) || orangeIn(style.backgroundImage)) {
      failures.push(`${label} rest background is an orange smear`);
    }

    if (formKind === "light") {
      if (bgLum != null && bgLum < 0.75) {
        failures.push(
          `${label} light form picked up a dark field background ${style.backgroundColor}`
        );
      }
      if (textLum != null && textLum > 0.62) {
        failures.push(
          `${label} typed/value text is near-white on a light field ` +
            `(fill=${style.webkitTextFillColor}, color=${style.color}, bg=${style.backgroundColor})`
        );
      }
      if (phLum != null && phLum > 0.72) {
        failures.push(
          `${label} placeholder is near-white on a light field ` +
            `(${phStyle.webkitTextFillColor || phStyle.color})`
        );
      }
      if (textContrast != null && textContrast < 3) {
        failures.push(`${label} value contrast ${textContrast.toFixed(2)} is too low`);
      }
      if (phContrast != null && phContrast < 1.35) {
        failures.push(`${label} placeholder contrast ${phContrast.toFixed(2)} is invisible`);
      }
      if (inkLum != null && inkLum > 0.7 && near(fg, ink, 10)) {
        failures.push(
          `${label} computed text matches dark-theme var(--ink) (${style.color})`
        );
      }
      if (
        inkLum != null &&
        inkLum > 0.7 &&
        near(fg, ink, 10) &&
        paper &&
        near(solidBg, paper, 14)
      ) {
        failures.push(
          `${label} dark-theme crm-input tokens won (var(--ink) on var(--paper))`
        );
      }
      if (/\bdark\b/.test(style.colorScheme) && !/\blight\b/.test(style.colorScheme)) {
        failures.push(`${label} color-scheme is still dark on a light form`);
      }
    } else {
      if (bgLum != null && bgLum > 0.5) {
        failures.push(
          `${label} dark card picked up a light field background ${style.backgroundColor}`
        );
      }
      if (textLum != null && textLum < 0.4) {
        failures.push(
          `${label} typed/value text is too dark on the dark card ` +
            `(${style.webkitTextFillColor}, ${style.color})`
        );
      }
      if (phContrast != null && phContrast < 1.25) {
        failures.push(`${label} placeholder contrast ${phContrast.toFixed(2)} is invisible`);
      }
    }
  }

  const copyNodes = [];
  for (const selector of spec.copy || []) {
    for (const el of scope.querySelectorAll(selector)) {
      if (!skipCopy(el)) copyNodes.push([selector, el]);
    }
  }

  for (const [selector, el] of copyNodes) {
    const style = getComputedStyle(el);
    const name = `${selector} "${(el.textContent || "").trim().slice(0, 48)}"`;
    if (orangeIn(style.backgroundColor) || orangeIn(style.backgroundImage)) {
      failures.push(`${name} has an orange highlight/ghost background`);
    }
    if (orangeIn(style.boxShadow)) {
      failures.push(`${name} has an orange halo (${style.boxShadow})`);
    }
  }

  const sheets = [...document.styleSheets].map((s) => s.href || "");
  if (!sheets.some((href) => href.includes("auth-family"))) {
    failures.push("auth-family.css is not loaded");
  }

  return {
    theme,
    formKind,
    failures,
    ink,
    paper,
    inputCount: inputs.length,
  };
}
"""
