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
