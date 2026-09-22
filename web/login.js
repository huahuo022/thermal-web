"use strict";

const form = document.getElementById("login-form");
const submit = document.getElementById("login-submit");
const errorBox = document.getElementById("login-error");

const params = new URLSearchParams(location.search);
const next = params.get("next") || "/";

function showError(message) {
  errorBox.textContent = message;
  errorBox.hidden = !message;
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  showError("");
  submit.disabled = true;
  submit.textContent = "登录中…";
  try {
    const response = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        username: document.getElementById("username").value,
        password: document.getElementById("password").value,
        next,
      }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.ok === false) {
      throw new Error(data.error || "登录失败");
    }
    location.href = data.redirect || next;
  } catch (error) {
    showError(error.message);
    submit.disabled = false;
    submit.textContent = "登录";
    document.getElementById("password").select();
  }
});
