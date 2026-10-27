const form = document.getElementById("login-form");
const errEl = document.getElementById("login-error");
const btn = document.getElementById("login-btn");
const password = document.getElementById("password");
const passwordToggle = document.querySelector(".login-pw-toggle");

passwordToggle.addEventListener("click", () => {
  const show = password.type === "password";
  password.type = show ? "text" : "password";
  passwordToggle.textContent = show ? "Hide" : "Show";
  passwordToggle.setAttribute("aria-label", show ? "Hide password" : "Show password");
  passwordToggle.setAttribute("aria-pressed", String(show));
});

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  errEl.hidden = true;
  btn.disabled = true;
  try {
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "ngrok-skip-browser-warning": "true",
      },
      body: JSON.stringify({
        username: document.getElementById("username").value.trim(),
        password: password.value,
      }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      throw new Error(data.detail || "Sign in failed");
    }
    window.location.href = "/";
  } catch (err) {
    errEl.textContent = err.message || "Sign in failed";
    errEl.hidden = false;
  } finally {
    btn.disabled = false;
  }
});
