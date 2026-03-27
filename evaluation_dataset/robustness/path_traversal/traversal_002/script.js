// Contact form validation and interaction
'use strict';

const form = document.querySelector('#contactForm');
const nameInput = document.getElementById('name');
const emailInput = document.getElementById('email');
const messageInput = document.getElementById('message');

function validateEmail(email) {
  const re = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
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
