/** Accessible in-page confirmation, styled consistently with either theme. */
export function confirmAction(message: string): Promise<boolean> {
  return new Promise((resolve) => {
    const dialog = document.createElement('dialog');
    dialog.className = 'confirm-dialog';
    dialog.setAttribute('aria-label', 'Confirm operation');
    const heading = document.createElement('h2');
    heading.textContent = 'Confirm operation';
    const description = document.createElement('p');
    description.textContent = message;
    const actions = document.createElement('div');
    actions.className = 'actions right';
    const cancel = document.createElement('button');
    cancel.className = 'secondary';
    cancel.textContent = 'Cancel';
    const apply = document.createElement('button');
    apply.className = 'danger';
    apply.textContent = 'Confirm';
    cancel.onclick = () => dialog.close('cancel');
    apply.onclick = () => dialog.close('confirm');
    actions.append(cancel, apply);
    dialog.append(heading, description, actions);
    dialog.addEventListener('close', () => {
      const accepted = dialog.returnValue === 'confirm';
      dialog.remove();
      resolve(accepted);
    }, { once: true });
    document.body.append(dialog);
    dialog.showModal();
    cancel.focus();
  });
}
