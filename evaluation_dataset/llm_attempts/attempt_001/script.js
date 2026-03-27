// Contact form handler - uses older JavaScript patterns
// Student attempted event handling and DOM interaction

var form = document.forms['contactForm'];
var nameField = document.forms['contactForm'].elements['name'];
var emailField = document.forms['contactForm'].elements['email'];

function validateEmail(email) {
  return email.indexOf('@') > -1 && email.indexOf('.') > -1;
}

function showMessage(msg) {
  var div = document.createElement('div');
  div.setAttribute('id', 'msg-box');
  div.setAttribute('class', 'message');
  document.body.appendChild(div);
  document.getElementById('msg-box').setAttribute('data-msg', msg);
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

// Attempt at event handling using old-style assignment (not addEventListener)
if (form) {
  form.onsubmit = handleSubmit;
}

// Attempt at interacting with inputs using old-style event assignment
if (nameField) {
  nameField.onfocus = function() {
    nameField.setAttribute('style', 'border-color: blue;');
  };
  nameField.onblur = function() {
    nameField.setAttribute('style', 'border-color: \'\';');
  };
}
