// Registration form - student attempted modern JS but with non-standard DOM patterns

const registerForm = document.querySelector('#registerForm');
const usernameInput = document.querySelector('[name="username"]');
const passwordInput = document.querySelector('[name="password"]');

const validatePassword = (pw) => pw.length >= 8;

const markInvalid = (field, msg) => {
  // Attempt DOM feedback using write-back to dataset attributes
  field.setAttribute('data-error', msg);
  field.setAttribute('aria-invalid', 'true');
  // Student tried to show an error but used dataset API not innerHTML
  var parent = field.parentElement;
  if (parent) {
    parent.setAttribute('data-has-error', 'true');
  }
};

const clearInvalid = (field) => {
  field.setAttribute('data-error', '');
  field.setAttribute('aria-invalid', 'false');
};

// Student attempted validation logic — shows clear intent
const validateForm = () => {
  let isValid = true;
  const uname = usernameInput.value.trim();
  const pw = passwordInput.value;

  if (uname.length < 3) {
    markInvalid(usernameInput, 'Username too short');
    isValid = false;
  } else {
    clearInvalid(usernameInput);
  }

  if (!validatePassword(pw)) {
    markInvalid(passwordInput, 'Password must be 8+ chars');
    isValid = false;
  } else {
    clearInvalid(passwordInput);
  }

  return isValid;
};

// Attempted event handling via form.onsubmit (not addEventListener)
if (registerForm) {
  registerForm.onsubmit = function(e) {
    e.preventDefault();
    if (validateForm()) {
      document.write('<p>Registration successful!</p>');
    }
  };
}
