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
