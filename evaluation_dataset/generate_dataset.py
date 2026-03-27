#!/usr/bin/env python3
"""Generate the AMS evaluation dataset.

Run this script once from the project root to create all synthetic student
submissions and write manifest.json:

    python evaluation_dataset/generate_dataset.py

The dataset covers:
  correct/       - Full-credit frontend submissions (expected overall = 1.0)
  partial/       - Partial-credit submissions (expected overall ~ 0.5)
  incorrect/     - Zero-credit submissions (expected overall = 0.0)
  frontend_only/ - Correct submissions evaluated with frontend profile
  robustness/    - Malformed, adversarial, and edge-case submissions

All submissions are synthetic and do not contain real student work.
"""
from __future__ import annotations

import json
import os
import shutil
import zipfile
from datetime import date
from pathlib import Path

# Dataset root is the directory containing this script
DATASET_ROOT = Path(__file__).parent


# ---------------------------------------------------------------------------
# HTML, CSS, JS templates — designed to satisfy frontend_interactive rules
# ---------------------------------------------------------------------------

# A fully compliant HTML page satisfying all html_rules
_FULL_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Student Contact Form</title>
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <header>
    <nav>
      <a href="index.html">Home</a>
      <a href="#contact">Contact</a>
    </nav>
  </header>
  <main>
    <section id="contact">
      <h1>Contact Us</h1>
      <h2>Send a Message</h2>
      <ul>
        <li>Fill in the form below</li>
        <li>Click Submit to send</li>
      </ul>
      <form id="contactForm" action="#" method="post">
        <label for="name">Name:</label>
        <input type="text" id="name" name="name" placeholder="Your name" required>
        <label for="email">Email:</label>
        <input type="email" id="email" name="email" placeholder="Your email" required>
        <label for="message">Message:</label>
        <textarea id="message" name="message" rows="5" required></textarea>
        <button type="submit">Submit</button>
      </form>
      <img src="logo.png" alt="Site logo" width="100">
    </section>
    <article>
      <p>Thank you for visiting our page.</p>
    </article>
  </main>
  <footer>
    <p>&copy; 2024 Student Project</p>
  </footer>
  <script src="script.js"></script>
</body>
</html>
"""

# A fully compliant CSS file satisfying all css_rules
_FULL_CSS = """\
/* CSS custom properties for maintainability */
:root {
  --primary-color: #3498db;
  --font-size-base: 16px;
  --spacing-md: 1rem;
}

/* Element selectors */
body {
  font-family: Arial, sans-serif;
  font-size: var(--font-size-base);
  margin: 0;
  padding: 0;
  color: #333;
  background-color: #f9f9f9;
}

h1 {
  color: var(--primary-color);
  font-size: 2rem;
}

/* Class selectors */
.container {
  max-width: 800px;
  margin: 0 auto;
  padding: var(--spacing-md);
}

.highlight {
  background-color: #fffacd;
  border-left: 4px solid var(--primary-color);
  padding: 0.5rem 1rem;
}

#contactForm {
  display: flex;
  flex-direction: column;
  gap: 1rem;
  max-width: 500px;
}

header {
  background-color: var(--primary-color);
  padding: 1rem;
  display: flex;
  justify-content: space-between;
  align-items: center;
}

nav a {
  color: #fff;
  text-decoration: none;
  margin-right: 1rem;
}

footer {
  text-align: center;
  padding: 1rem;
  background-color: #eee;
}

input, textarea {
  padding: 0.5rem;
  border: 1px solid #ccc;
  border-radius: 4px;
  font-size: 1rem;
}

button {
  background-color: var(--primary-color);
  color: #fff;
  border: none;
  padding: 0.75rem 1.5rem;
  cursor: pointer;
  font-size: 1rem;
  border-radius: 4px;
}

/* Responsive layout */
@media (max-width: 768px) {
  .container {
    padding: 0.5rem;
  }
  header {
    flex-direction: column;
  }
  #contactForm {
    max-width: 100%;
  }
}

@media (max-width: 480px) {
  h1 {
    font-size: 1.5rem;
  }
  button {
    width: 100%;
  }
}
"""

# A fully compliant JS file satisfying all js_rules
_FULL_JS = """\
// Contact form validation and interaction
'use strict';

const form = document.querySelector('#contactForm');
const nameInput = document.getElementById('name');
const emailInput = document.getElementById('email');
const messageInput = document.getElementById('message');

function validateEmail(email) {
  const re = /^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$/;
  return re.test(email);
}

function showError(input, message) {
  const existingError = input.nextElementSibling;
  if (existingError && existingError.classList.contains('error-msg')) {
    existingError.textContent = message;
  } else {
    const errorEl = document.createElement('span');
    errorEl.classList.add('error-msg');
    errorEl.style.color = 'red';
    errorEl.textContent = message;
    input.parentNode.insertBefore(errorEl, input.nextSibling);
  }
}

function clearErrors() {
  const errors = document.querySelectorAll('.error-msg');
  errors.forEach(el => el.remove());
}

function handleSubmit(event) {
  event.preventDefault();
  clearErrors();

  let valid = true;
  const nameVal = nameInput.value.trim();
  const emailVal = emailInput.value.trim();
  const msgVal = messageInput.value.trim();

  if (!nameVal) {
    showError(nameInput, 'Name is required.');
    valid = false;
  }

  if (!emailVal || !validateEmail(emailVal)) {
    showError(emailInput, 'A valid email address is required.');
    valid = false;
  }

  if (!msgVal) {
    showError(messageInput, 'Message cannot be empty.');
    valid = false;
  }

  if (valid) {
    const confirmEl = document.createElement('p');
    confirmEl.textContent = `Thank you, ${nameVal}! Your message has been sent.`;
    confirmEl.style.color = 'green';
    form.innerHTML = '';
    form.appendChild(confirmEl);
  }
}

// Register event listener
if (form) {
  form.addEventListener('submit', handleSubmit);
}

// Highlight empty inputs on blur
const inputs = document.querySelectorAll('input, textarea');
for (let i = 0; i < inputs.length; i++) {
  inputs[i].addEventListener('blur', function () {
    if (this.value.trim() === '') {
      this.style.borderColor = 'red';
    } else {
      this.style.borderColor = '';
    }
  });
}
"""

# ---------------------------------------------------------------------------
# Variant templates for diversity
# ---------------------------------------------------------------------------

_FULL_HTML_V2 = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Registration Page</title>
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <header>
    <nav>
      <a href="#">Home</a>
      <a href="#register">Register</a>
    </nav>
  </header>
  <main>
    <section id="register">
      <h1>Create Account</h1>
      <h2>Fill in your details</h2>
      <ul>
        <li>All fields are required</li>
        <li>Password must be 8+ characters</li>
      </ul>
      <form id="registerForm" method="post" action="#">
        <label for="username">Username:</label>
        <input type="text" id="username" name="username" required>
        <label for="password">Password:</label>
        <input type="password" id="password" name="password" required>
        <label for="confirm">Confirm Password:</label>
        <input type="password" id="confirm" name="confirm" required>
        <button type="submit">Register</button>
      </form>
      <img src="banner.png" alt="Registration banner">
    </section>
    <aside>
      <p>Already have an account? <a href="#">Log in</a></p>
    </aside>
  </main>
  <footer>
    <p>Student Registration System &mdash; 2024</p>
  </footer>
  <script src="script.js"></script>
</body>
</html>
"""

_FULL_CSS_V2 = """\
:root {
  --accent: #e74c3c;
  --base-font: 'Segoe UI', sans-serif;
}

body {
  font-family: var(--base-font);
  background: #fff;
  color: #222;
  margin: 0;
  padding: 0;
}

h1 { color: var(--accent); font-size: 1.8rem; }
h2 { font-size: 1.2rem; color: #555; }

.wrapper {
  max-width: 600px;
  margin: 2rem auto;
  padding: 1rem;
}

.card {
  background: #fafafa;
  border: 1px solid #ddd;
  border-radius: 8px;
  padding: 1.5rem;
}

header {
  background: var(--accent);
  padding: 0.75rem 1.5rem;
  display: flex;
  align-items: center;
}

nav a {
  color: white;
  margin-right: 1rem;
  text-decoration: none;
}

#registerForm {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

input {
  border: 1px solid #ccc;
  padding: 0.5rem;
  font-size: 1rem;
  border-radius: 4px;
}

button {
  background: var(--accent);
  color: white;
  border: none;
  padding: 0.6rem 1.2rem;
  cursor: pointer;
  font-size: 1rem;
  border-radius: 4px;
}

footer { text-align: center; padding: 1rem; background: #eee; }

@media (max-width: 600px) {
  .wrapper { margin: 0.5rem; padding: 0.5rem; }
  h1 { font-size: 1.4rem; }
}
"""

_FULL_JS_V2 = """\
'use strict';

const regForm = document.querySelector('#registerForm');
const pwInput = document.getElementById('password');
const confirmInput = document.getElementById('confirm');
const usernameInput = document.getElementById('username');

function validatePassword(pw) {
  return pw.length >= 8;
}

function displayFeedback(element, msg, isError) {
  let fb = element.nextElementSibling;
  if (!fb || !fb.classList.contains('fb')) {
    fb = document.createElement('small');
    fb.classList.add('fb');
    element.parentNode.insertBefore(fb, element.nextSibling);
  }
  fb.textContent = msg;
  fb.style.color = isError ? '#c0392b' : '#27ae60';
}

function handleRegistration(e) {
  e.preventDefault();
  let ok = true;

  const uname = usernameInput.value.trim();
  const pw = pwInput.value;
  const confirm = confirmInput.value;

  if (uname.length < 3) {
    displayFeedback(usernameInput, 'Username must be at least 3 characters.', true);
    ok = false;
  } else {
    displayFeedback(usernameInput, '✓', false);
  }

  if (!validatePassword(pw)) {
    displayFeedback(pwInput, 'Password must be at least 8 characters.', true);
    ok = false;
  }

  if (pw !== confirm) {
    displayFeedback(confirmInput, 'Passwords do not match.', true);
    ok = false;
  }

  if (ok) {
    const msg = document.createElement('p');
    msg.textContent = `Account created for ${uname}!`;
    msg.style.color = 'green';
    regForm.replaceWith(msg);
  }
}

if (regForm) {
  regForm.addEventListener('submit', handleRegistration);
}

const allInputs = document.querySelectorAll('input');
for (let i = 0; i < allInputs.length; i++) {
  const inp = allInputs[i];
  inp.addEventListener('focus', function () {
    this.style.outline = '2px solid var(--accent, #e74c3c)';
  });
  inp.addEventListener('blur', function () {
    this.style.outline = '';
  });
}
"""

_FULL_HTML_V3 = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Product Feedback</title>
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <header>
    <nav>
      <a href="#">Home</a>
      <a href="#feedback">Feedback</a>
      <a href="#faq">FAQ</a>
    </nav>
  </header>
  <main>
    <section id="feedback">
      <h1>Product Feedback</h1>
      <h2>Share your experience</h2>
      <ul>
        <li>Rating is required</li>
        <li>Comments are optional</li>
      </ul>
      <form id="feedbackForm" method="post" action="#">
        <label for="product">Product:</label>
        <input type="text" id="product" name="product" required>
        <label for="rating">Rating (1-5):</label>
        <input type="number" id="rating" name="rating" min="1" max="5" required>
        <label for="comment">Comment:</label>
        <textarea id="comment" name="comment" rows="4"></textarea>
        <button type="submit">Submit Feedback</button>
      </form>
      <img src="product.png" alt="Product image">
    </section>
    <article id="faq">
      <h2>Frequently Asked Questions</h2>
      <p>Your feedback helps us improve our products.</p>
    </article>
  </main>
  <footer>
    <p>Feedback Portal &copy; 2024</p>
  </footer>
  <script src="script.js"></script>
</body>
</html>
"""

_FULL_CSS_V3 = """\
:root {
  --green: #2ecc71;
  --dark: #2c3e50;
}

* { box-sizing: border-box; }

body {
  font-family: Georgia, serif;
  background: #fdfdfd;
  color: var(--dark);
  margin: 0;
}

h1 { color: var(--green); font-size: 2rem; }
h2 { color: var(--dark); font-size: 1.3rem; }

.page {
  width: 90%;
  max-width: 700px;
  margin: 0 auto;
}

.form-group {
  display: flex;
  flex-direction: column;
  margin-bottom: 1rem;
}

header {
  background: var(--dark);
  color: white;
  padding: 1rem;
  display: flex;
  justify-content: space-between;
}

nav a { color: white; margin: 0 0.5rem; text-decoration: none; }

#feedbackForm {
  background: #fff;
  padding: 1.5rem;
  border: 1px solid #ddd;
  border-radius: 6px;
}

input, textarea {
  padding: 0.5rem;
  font-size: 1rem;
  border: 1px solid #bbb;
  border-radius: 4px;
}

button {
  background: var(--green);
  color: white;
  border: none;
  padding: 0.7rem 1.5rem;
  cursor: pointer;
  font-size: 1rem;
  border-radius: 4px;
}

footer { background: #f0f0f0; text-align: center; padding: 0.75rem; margin-top: 2rem; }

@media (max-width: 600px) {
  .page { width: 100%; padding: 0.5rem; }
  h1 { font-size: 1.5rem; }
  button { width: 100%; }
}
"""

_FULL_JS_V3 = """\
'use strict';

const fbForm = document.querySelector('#feedbackForm');
const ratingInput = document.getElementById('rating');
const productInput = document.getElementById('product');

function isValidRating(val) {
  const n = parseInt(val, 10);
  return !isNaN(n) && n >= 1 && n <= 5;
}

function setFieldError(field, message) {
  field.style.borderColor = 'red';
  let errSpan = field.parentNode.querySelector('.err');
  if (!errSpan) {
    errSpan = document.createElement('span');
    errSpan.className = 'err';
    errSpan.style.color = 'red';
    errSpan.style.fontSize = '0.85rem';
    field.parentNode.appendChild(errSpan);
  }
  errSpan.textContent = message;
}

function clearFieldError(field) {
  field.style.borderColor = '';
  const errSpan = field.parentNode.querySelector('.err');
  if (errSpan) errSpan.remove();
}

function onSubmit(e) {
  e.preventDefault();
  let passed = true;

  clearFieldError(productInput);
  clearFieldError(ratingInput);

  if (!productInput.value.trim()) {
    setFieldError(productInput, 'Product name is required.');
    passed = false;
  }

  if (!isValidRating(ratingInput.value)) {
    setFieldError(ratingInput, 'Rating must be between 1 and 5.');
    passed = false;
  }

  if (passed) {
    const thanks = document.createElement('p');
    const product = productInput.value.trim();
    const rating = ratingInput.value;
    thanks.innerHTML = `Thank you for rating <strong>${product}</strong> ${rating}/5!`;
    thanks.style.color = 'green';
    fbForm.replaceWith(thanks);
  }
}

if (fbForm) {
  fbForm.addEventListener('submit', onSubmit);
}

if (ratingInput) {
  ratingInput.addEventListener('input', function () {
    const val = parseInt(this.value, 10);
    if (val < 1 || val > 5) {
      this.style.color = 'red';
    } else {
      this.style.color = '';
    }
  });
}
"""

# ---------------------------------------------------------------------------
# Partial submissions: HTML+CSS pass, JS empty/absent
# ---------------------------------------------------------------------------

_PARTIAL_HTML_1 = _FULL_HTML  # Full HTML
_PARTIAL_CSS_1 = _FULL_CSS    # Full CSS
_PARTIAL_JS_1 = ""            # Empty JS — scores 0

_PARTIAL_HTML_2 = _FULL_HTML_V2
_PARTIAL_CSS_2 = _FULL_CSS_V2
_PARTIAL_JS_2 = ""            # Empty JS

_PARTIAL_HTML_3 = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Simple Page</title>
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <header>
    <nav><a href="#">Home</a></nav>
  </header>
  <main>
    <section>
      <h1>Hello World</h1>
      <ul><li>Item one</li><li>Item two</li></ul>
      <form id="simpleForm" method="post">
        <label for="q">Query:</label>
        <input type="text" id="q" name="q">
        <button type="submit">Go</button>
      </form>
      <img src="pic.png" alt="Picture">
    </section>
    <article><p>Some text here.</p></article>
  </main>
  <footer><p>Footer</p></footer>
  <script src="script.js"></script>
</body>
</html>
"""
_PARTIAL_CSS_3 = """\
body { font-family: sans-serif; margin: 0; padding: 0; color: #333; }
h1 { font-size: 1.5rem; color: #0077cc; }
.box { background: #f0f0f0; padding: 1rem; border: 1px solid #ccc; }
header { background: #0077cc; padding: 0.5rem; display: flex; }
nav a { color: white; text-decoration: none; }
@media (max-width: 600px) { body { font-size: 14px; } }
"""
_PARTIAL_JS_3 = ""  # Empty JS

# ---------------------------------------------------------------------------
# Incorrect submissions: empty files → all scores 0
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# LLM attempt submissions: fail static checks but show clear student intent
#
# These are designed to trigger partial_allowed=True rules so the LLM
# can be tested on whether it awards partial credit.
#
# Targets:
#   js.has_event_listener  (partial_allowed=True) — use onclick= instead of addEventListener
#   js.has_dom_manipulation (partial_allowed=True) — use setAttribute/document.write instead of innerHTML
#   js.has_const_let       (partial_allowed=True) — use var only
#   css.has_media_queries  (partial_allowed=True) — attempt commented-out or malformed @media
# ---------------------------------------------------------------------------

# Attempt 1: JS uses var + onsubmit + document.write (clear intent, legacy patterns)
_ATTEMPT_JS_1 = """\
// Contact form handler - uses older JavaScript patterns
// Student attempted event handling and DOM interaction

var form = document.forms['contactForm'];
var nameField = document.forms['contactForm'].elements['name'];
var emailField = document.forms['contactForm'].elements['email'];

function validateEmail(email) {
  return email.indexOf('@') > -1 && email.indexOf('.') > -1;
}

function showMessage(msg) {
  // Intent is clear, but implementation uses a legacy rendering approach.
  document.write('<p class="legacy-msg">' + msg + '</p>');
}

function handleSubmit() {
  var name = nameField.value;
  var email = emailField.value;
  if (!name || !validateEmail(email)) {
    showMessage('Please fill in all fields correctly.');
    return false;
  }
  showMessage('Form submitted for ' + name);
  return false;
}

// Attempt at event handling using old-style assignment (legacy pattern)
if (form) {
  form.onsubmit = handleSubmit;
}

if (nameField) {
  nameField.onfocus = function() {
    // Legacy feedback path instead of modern classList / style updates.
    document.write('<small>Editing name</small>');
  };
}
"""

# Attempt 2: JS keeps modern query APIs but uses var + onsubmit + document.write
_ATTEMPT_JS_2 = """\
// Registration form - student attempted validation with legacy event handling

var registerForm = document.querySelector('#registerForm');
var usernameInput = document.querySelector('[name="username"]');
var passwordInput = document.querySelector('[name="password"]');

function validatePassword(pw) {
  return pw.length >= 8;
}

function markInvalid(msg) {
  // Legacy output path that still shows student intent.
  document.write('<p class="warn">' + msg + '</p>');
}

// Student attempted validation logic — shows clear intent
function validateForm() {
  var isValid = true;
  var uname = usernameInput.value.trim();
  var pw = passwordInput.value;

  if (uname.length < 3) {
    markInvalid('Username too short');
    isValid = false;
  }

  if (!validatePassword(pw)) {
    markInvalid('Password must be 8+ chars');
    isValid = false;
  }

  return isValid;
}

// Attempted event handling via form.onsubmit (legacy pattern)
if (registerForm) {
  registerForm.onsubmit = function(e) {
    e.preventDefault();
    if (validateForm()) {
      document.write('<p>Registration successful!</p>');
    }
  };
}
"""

# Attempt 3: CSS with commented-out media queries and partially correct flexbox
_ATTEMPT_CSS_3 = """\
/* Student attempted responsive design and flexbox layout */

:root {
  --blue: #3498db;
  --base-font: 16px;
}

body {
  font-family: Arial, sans-serif;
  font-size: var(--base-font);
  margin: 0;
  padding: 0;
  color: #333;
}

h1 {
  color: var(--blue);
  font-size: 2rem;
}

.container {
  max-width: 800px;
  margin: 0 auto;
  padding: 1rem;
}

/* Student intended flexible layout but implementation is incomplete */
header {
  background-color: var(--blue);
  padding: 1rem;
  display: block;
}

nav a {
  color: white;
  text-decoration: none;
  margin-right: 1rem;
}

#contactForm {
  max-width: 500px;
  /* Student attempted flex here but forgot to close properly */
  display: block;
  padding: 1rem;
}

input, textarea {
  padding: 0.5rem;
  border: 1px solid #ccc;
  font-size: 1rem;
  width: 100%;
  box-sizing: border-box;
}

button {
  background-color: var(--blue);
  color: white;
  border: none;
  padding: 0.75rem 1.5rem;
  cursor: pointer;
  font-size: 1rem;
}

footer {
  text-align: center;
  padding: 1rem;
  background: #eee;
}

/* Student attempted media queries but syntax is incomplete */
/* @media (max-width: 768px) {
  .container {
    padding: 0.5rem;
  }
}  */

/* TODO: add responsive breakpoints */
"""

_INCORRECT_HTML_EMPTY = ""      # Completely empty
_INCORRECT_CSS_EMPTY = ""
_INCORRECT_JS_EMPTY = ""

# Minimal HTML structure but NO required elements (no form, no semantic, no heading)
_INCORRECT_HTML_NO_ELEMENTS = """\
<html>
<head><title>Page</title></head>
<body>
<p>Welcome.</p>
</body>
</html>
"""
_INCORRECT_CSS_MINIMAL = "p { color: black; }"
_INCORRECT_JS_EMPTY2 = ""

# ---------------------------------------------------------------------------
# Robustness: broken syntax
# ---------------------------------------------------------------------------

_BROKEN_HTML = """\
<!DOCTYPE html>
<html lang="en"
<head>
  <meta charset="UTF-8>
  <title>Broken Page</title
</head
<body>
  <h1>Unclosed tags everywhere
  <form>
    <input type="text" name="x"
    <label for="x">Label
  </form
  This is malformed HTML with unclosed tags and missing quotes.
  <div class="oops
</body>
"""

_BROKEN_CSS = """\
/* Broken CSS */
body {
  font-family: sans-serif
  color: red    /* missing semicolons */
  background: blue
}

.missing-brace {
  display: flex
  /* brace never closed

h1 {
  color: green;
  /* unclosed comment
}
"""

_BROKEN_JS = """\
// Broken JavaScript with syntax errors
function doSomething( {
  const x = ;
  if (x > {
    console.log('broken'
  }
  return x +;
}

const y = doSomething();
let z = y + )3;
addEventListener('click', () => {;
"""

# Path traversal attempt in HTML href
_PATH_TRAVERSAL_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Traversal Attempt</title>
  <link rel="stylesheet" href="../../../etc/passwd">
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <h1>Test Page</h1>
  <form>
    <input type="text" name="user">
    <label for="user">User</label>
    <button>Submit</button>
  </form>
  <script src="../../../etc/shadow"></script>
  <script src="script.js"></script>
  <img src="../../sensitive.png" alt="traversal test">
</body>
</html>
"""

# ---------------------------------------------------------------------------
# File creation helpers
# ---------------------------------------------------------------------------

def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _make_submission(base: Path, files: dict[str, str]) -> None:
    """Create a submission directory with the given filename → content mapping."""
    base.mkdir(parents=True, exist_ok=True)
    for filename, content in files.items():
        _write(base / filename, content)


# ---------------------------------------------------------------------------
# Dataset creation
# ---------------------------------------------------------------------------

def create_dataset() -> list[dict]:
    """Create all submission directories and return the manifest entries list."""
    entries: list[dict] = []

    # ── CORRECT submissions (expected_overall = 1.0) ──────────────────────
    for idx, (html, css, js, note) in enumerate([
        (_FULL_HTML,    _FULL_CSS,    _FULL_JS,    "Complete contact form with validation"),
        (_FULL_HTML_V2, _FULL_CSS_V2, _FULL_JS_V2, "Registration form with password validation"),
        (_FULL_HTML_V3, _FULL_CSS_V3, _FULL_JS_V3, "Product feedback form with rating validation"),
    ], start=1):
        sid = f"correct_{idx:03d}"
        _make_submission(DATASET_ROOT / "correct" / sid, {
            "index.html": html,
            "style.css":  css,
            "script.js":  js,
        })
        entries.append({
            "id": sid,
            "path": f"correct/{sid}",
            "category": "correct",
            "profile": "frontend",
            "expected_overall": 1.0,
            "expected_components": {"html": 1.0, "css": 1.0, "js": 1.0},
            "notes": note,
        })

    # ── PARTIAL submissions (HTML+CSS pass, JS empty → overall ~0.67 → label 0.5) ──
    for idx, (html, css, js, note) in enumerate([
        (_PARTIAL_HTML_1, _PARTIAL_CSS_1, _PARTIAL_JS_1, "Full HTML+CSS, empty JS file"),
        (_PARTIAL_HTML_2, _PARTIAL_CSS_2, _PARTIAL_JS_2, "Registration HTML+CSS, no JS"),
        (_PARTIAL_HTML_3, _PARTIAL_CSS_3, _PARTIAL_JS_3, "Minimal HTML+CSS, no JS"),
    ], start=1):
        sid = f"partial_{idx:03d}"
        files = {"index.html": html, "style.css": css}
        if js is not None:  # create the JS file even if empty
            files["script.js"] = js
        _make_submission(DATASET_ROOT / "partial" / sid, files)
        entries.append({
            "id": sid,
            "path": f"partial/{sid}",
            "category": "partial",
            "profile": "frontend",
            "expected_overall": 0.5,
            "expected_components": {"html": 1.0, "css": 1.0, "js": 0.0},
            "notes": note,
        })

    # ── INCORRECT submissions (expected_overall = 0.0) ────────────────────
    _make_submission(DATASET_ROOT / "incorrect" / "incorrect_001", {
        "index.html": _INCORRECT_HTML_EMPTY,
        "style.css":  _INCORRECT_CSS_EMPTY,
        "script.js":  _INCORRECT_JS_EMPTY,
    })
    entries.append({
        "id": "incorrect_001",
        "path": "incorrect/incorrect_001",
        "category": "incorrect",
        "profile": "frontend",
        "expected_overall": 0.0,
        "expected_components": {"html": 0.0, "css": 0.0, "js": 0.0},
        "notes": "All files completely empty",
    })

    _make_submission(DATASET_ROOT / "incorrect" / "incorrect_002", {
        "index.html": _INCORRECT_HTML_NO_ELEMENTS,
        "style.css":  _INCORRECT_CSS_MINIMAL,
        "script.js":  _INCORRECT_JS_EMPTY2,
    })
    entries.append({
        "id": "incorrect_002",
        "path": "incorrect/incorrect_002",
        "category": "incorrect",
        "profile": "frontend",
        "expected_overall": 0.0,
        "expected_components": {"html": 0.0, "css": 0.0, "js": 0.0},
        "notes": "Minimal HTML structure, no required elements, no JS",
    })

    _make_submission(DATASET_ROOT / "incorrect" / "incorrect_003", {
        "index.html": "",
        "style.css":  "",
        "script.js":  "",
    })
    entries.append({
        "id": "incorrect_003",
        "path": "incorrect/incorrect_003",
        "category": "incorrect",
        "profile": "frontend",
        "expected_overall": 0.0,
        "expected_components": {"html": 0.0, "css": 0.0, "js": 0.0},
        "notes": "All files present but zero bytes",
    })

    # ── FRONTEND-ONLY (correct frontend submissions, profile=frontend) ─────
    for idx, (html, css, js, note) in enumerate([
        (_FULL_HTML,    _FULL_CSS,    _FULL_JS,    "Complete frontend form, frontend profile"),
        (_FULL_HTML_V3, _FULL_CSS_V3, _FULL_JS_V3, "Feedback form, frontend profile"),
    ], start=1):
        sid = f"frontend_only_{idx:03d}"
        _make_submission(DATASET_ROOT / "frontend_only" / sid, {
            "index.html": html,
            "style.css":  css,
            "script.js":  js,
        })
        entries.append({
            "id": sid,
            "path": f"frontend_only/{sid}",
            "category": "frontend_only",
            "profile": "frontend",
            "expected_overall": 1.0,
            "expected_components": {"html": 1.0, "css": 1.0, "js": 1.0},
            "notes": note,
        })

    # ── LLM ATTEMPT submissions ───────────────────────────────────────────
    # These fail static checks on partial_allowed=True rules but show intent,
    # allowing the LLM to award partial credit. Used for llm_marking evaluation.

    # Attempt 1: Full HTML+CSS, JS uses var + onclick + setAttribute (no addEventListener)
    _make_submission(DATASET_ROOT / "llm_attempts" / "attempt_001", {
        "index.html": _FULL_HTML,
        "style.css":  _FULL_CSS,
        "script.js":  _ATTEMPT_JS_1,
    })
    entries.append({
        "id": "attempt_001",
        "path": "llm_attempts/attempt_001",
        "category": "llm_attempt",
        "profile": "frontend",
        "expected_overall": None,
        "expected_components": {},
        "notes": (
            "Full HTML+CSS, JS uses var/onclick/setAttribute. "
            "Fails js.has_event_listener and js.has_dom_manipulation static checks. "
            "LLM should detect student intent and award partial credit."
        ),
    })

    # Attempt 2: Full HTML+CSS, JS uses querySelector+const but onclick and document.write
    _make_submission(DATASET_ROOT / "llm_attempts" / "attempt_002", {
        "index.html": _FULL_HTML_V2,
        "style.css":  _FULL_CSS_V2,
        "script.js":  _ATTEMPT_JS_2,
    })
    entries.append({
        "id": "attempt_002",
        "path": "llm_attempts/attempt_002",
        "category": "llm_attempt",
        "profile": "frontend",
        "expected_overall": None,
        "expected_components": {},
        "notes": (
            "HTML+CSS correct, JS uses onsubmit= and document.write. "
            "Fails js.has_event_listener and js.has_dom_manipulation. "
            "Has const/let and DOM queries. LLM should award partial credit."
        ),
    })

    # Attempt 3: Full HTML+legacy JS, CSS lacks media queries (commented out) and full flexbox
    _make_submission(DATASET_ROOT / "llm_attempts" / "attempt_003", {
        "index.html": _FULL_HTML,
        "style.css":  _ATTEMPT_CSS_3,
      "script.js":  _ATTEMPT_JS_1,
    })
    entries.append({
        "id": "attempt_003",
        "path": "llm_attempts/attempt_003",
        "category": "llm_attempt",
        "profile": "frontend",
        "expected_overall": None,
        "expected_components": {},
        "notes": (
          "Full HTML+legacy JS, CSS has flexbox on one element only and no @media queries "
          "(commented out). Fails css.has_media_query and JS modern-event checks. "
            "LLM should detect the attempt and award partial credit."
        ),
    })

    # ── ROBUSTNESS: missing files ──────────────────────────────────────────
    # Empty directory
    (DATASET_ROOT / "robustness" / "missing_files" / "empty_submission").mkdir(
        parents=True, exist_ok=True
    )
    entries.append({
        "id": "rob_missing_empty",
        "path": "robustness/missing_files/empty_submission",
        "category": "robustness/missing_files",
        "profile": "frontend",
        "expected_overall": None,
        "expected_components": {},
        "notes": "Empty directory — no files at all",
    })

    # CSS+JS but no HTML
    _make_submission(DATASET_ROOT / "robustness" / "missing_files" / "no_html", {
        "style.css":  _FULL_CSS,
        "script.js":  _FULL_JS,
    })
    entries.append({
        "id": "rob_missing_no_html",
        "path": "robustness/missing_files/no_html",
        "category": "robustness/missing_files",
        "profile": "frontend",
        "expected_overall": None,
        "expected_components": {},
        "notes": "Has CSS and JS but no HTML file",
    })

    # HTML+JS but no CSS
    _make_submission(DATASET_ROOT / "robustness" / "missing_files" / "no_css", {
        "index.html": _FULL_HTML,
        "script.js":  _FULL_JS,
    })
    entries.append({
        "id": "rob_missing_no_css",
        "path": "robustness/missing_files/no_css",
        "category": "robustness/missing_files",
        "profile": "frontend",
        "expected_overall": None,
        "expected_components": {},
        "notes": "Has HTML and JS but no CSS file",
    })

    # ── ROBUSTNESS: broken syntax ──────────────────────────────────────────
    _make_submission(DATASET_ROOT / "robustness" / "broken_syntax" / "broken_html", {
        "index.html": _BROKEN_HTML,
        "style.css":  _FULL_CSS,
        "script.js":  _FULL_JS,
    })
    entries.append({
        "id": "rob_syntax_broken_html",
        "path": "robustness/broken_syntax/broken_html",
        "category": "robustness/broken_syntax",
        "profile": "frontend",
        "expected_overall": None,
        "expected_components": {},
        "notes": "Malformed HTML with unclosed tags and missing quotes",
    })

    _make_submission(DATASET_ROOT / "robustness" / "broken_syntax" / "broken_css", {
        "index.html": _FULL_HTML,
        "style.css":  _BROKEN_CSS,
        "script.js":  _FULL_JS,
    })
    entries.append({
        "id": "rob_syntax_broken_css",
        "path": "robustness/broken_syntax/broken_css",
        "category": "robustness/broken_syntax",
        "profile": "frontend",
        "expected_overall": None,
        "expected_components": {},
        "notes": "CSS file with syntax errors (missing semicolons, unclosed blocks)",
    })

    _make_submission(DATASET_ROOT / "robustness" / "broken_syntax" / "broken_js", {
        "index.html": _FULL_HTML,
        "style.css":  _FULL_CSS,
        "script.js":  _BROKEN_JS,
    })
    entries.append({
        "id": "rob_syntax_broken_js",
        "path": "robustness/broken_syntax/broken_js",
        "category": "robustness/broken_syntax",
        "profile": "frontend",
        "expected_overall": None,
        "expected_components": {},
        "notes": "JS file with syntax errors (unclosed function, invalid expressions)",
    })

    # ── ROBUSTNESS: path traversal ─────────────────────────────────────────
    _make_submission(DATASET_ROOT / "robustness" / "path_traversal" / "traversal_001", {
        "index.html": _PATH_TRAVERSAL_HTML,
        "style.css":  _FULL_CSS,
        "script.js":  _FULL_JS,
    })
    entries.append({
        "id": "rob_traversal_001",
        "path": "robustness/path_traversal/traversal_001",
        "category": "robustness/path_traversal",
        "profile": "frontend",
        "expected_overall": None,
        "expected_components": {},
        "notes": "HTML references ../../../etc/passwd in href/src attributes",
    })

    # Submission with ../ in filename path (created in nested dir)
    nested = DATASET_ROOT / "robustness" / "path_traversal" / "traversal_002"
    nested.mkdir(parents=True, exist_ok=True)
    _write(nested / "index.html", _FULL_HTML.replace(
        'href="style.css"', 'href="../style.css"'
    ).replace(
        'src="script.js"', 'src="../script.js"'
    ))
    _write(nested / "style.css", _FULL_CSS)
    _write(nested / "script.js", _FULL_JS)
    entries.append({
        "id": "rob_traversal_002",
        "path": "robustness/path_traversal/traversal_002",
        "category": "robustness/path_traversal",
        "profile": "frontend",
        "expected_overall": None,
        "expected_components": {},
        "notes": "HTML links to ../style.css and ../script.js (relative path escape)",
    })

    # ── ROBUSTNESS: adversarial ────────────────────────────────────────────
    # Deeply nested directory structure
    deep = DATASET_ROOT / "robustness" / "adversarial" / "deep_nesting" / "a" / "b" / "c" / "d"
    deep.mkdir(parents=True, exist_ok=True)
    _write(deep / "index.html", _FULL_HTML)
    _write(deep / "style.css", _FULL_CSS)
    _write(deep / "script.js", _FULL_JS)
    entries.append({
        "id": "rob_adversarial_deep_nesting",
        "path": "robustness/adversarial/deep_nesting",
        "category": "robustness/adversarial",
        "profile": "frontend",
        "expected_overall": None,
        "expected_components": {},
        "notes": "Files buried 5 directories deep (a/b/c/d/index.html)",
    })

    # Submission with binary/non-text junk file alongside valid files
    junk_dir = DATASET_ROOT / "robustness" / "adversarial" / "with_junk_files"
    junk_dir.mkdir(parents=True, exist_ok=True)
    _write(junk_dir / "index.html", _FULL_HTML)
    _write(junk_dir / "style.css", _FULL_CSS)
    _write(junk_dir / "script.js", _FULL_JS)
    # Write a small fake binary file
    (junk_dir / "binary_data.bin").write_bytes(bytes(range(256)) * 4)
    (junk_dir / ".DS_Store").write_text("fake mac junk", encoding="utf-8")
    (junk_dir / "Thumbs.db").write_bytes(b"\x00\x01\x02\x03" * 100)
    entries.append({
        "id": "rob_adversarial_junk_files",
        "path": "robustness/adversarial/with_junk_files",
        "category": "robustness/adversarial",
        "profile": "frontend",
        "expected_overall": None,
        "expected_components": {},
        "notes": "Valid submission with binary junk files (.bin, .DS_Store, Thumbs.db)",
    })

    return entries


def main() -> None:
    print(f"Generating evaluation dataset in: {DATASET_ROOT}")

    # Clean up existing generated content (keep generate_dataset.py and manifest.json)
    for subdir in ["correct", "partial", "incorrect", "frontend_only", "robustness", "llm_attempts"]:
        target = DATASET_ROOT / subdir
        if target.exists():
            shutil.rmtree(target)

    entries = create_dataset()

    manifest = {
        "version": "1.0",
        "created_at": str(date.today()),
        "description": (
            "AMS evaluation dataset with labelled synthetic submissions. "
            "Used for accuracy, consistency, robustness, and LLM marking evaluation."
        ),
        "submissions": entries,
    }

    manifest_path = DATASET_ROOT / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # Print summary
    categories: dict[str, int] = {}
    for e in entries:
        cat = e["category"]
        categories[cat] = categories.get(cat, 0) + 1

    print(f"\nDataset created: {len(entries)} submissions")
    for cat, count in sorted(categories.items()):
        print(f"  {cat:<40} {count:>3}")
    print(f"\nManifest written to: {manifest_path}")


if __name__ == "__main__":
    main()
