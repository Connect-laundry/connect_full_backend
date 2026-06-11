(function () {
  "use strict";

  // ---- 1. Service Worker Registration ----
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', function () {
      navigator.serviceWorker.register('/service-worker.js')
        .then(function (reg) {
          console.log('[PWA] ServiceWorker registered with scope:', reg.scope);
          
          // Check for updates
          reg.onupdatefound = function () {
            const installingWorker = reg.installing;
            if (installingWorker) {
              installingWorker.onstatechange = function () {
                if (installingWorker.state === 'installed' && navigator.serviceWorker.controller) {
                  console.log('[PWA] New version available!');
                }
              };
            }
          };
        })
        .catch(function (err) {
          console.error('[PWA] ServiceWorker registration failed:', err);
        });
    });
  }

  // ---- 2. Install Prompt Handling ----
  let deferredPrompt = null;
  let installBtn = null;

  window.addEventListener('beforeinstallprompt', function (e) {
    // Prevent Chrome from automatically showing the prompt
    e.preventDefault();
    // Stash the event so it can be triggered later.
    deferredPrompt = e;
    
    console.log('[PWA] App is installable. beforeinstallprompt fired.');
    showInstallButton();
  });

  function showInstallButton() {
    if (installBtn) {
      installBtn.style.display = 'inline-flex';
      return;
    }

    // Build the install button
    installBtn = document.createElement('button');
    installBtn.type = 'button';
    installBtn.className = 'aops-btn aops-btn-install';
    installBtn.id = 'pwa-install-btn';
    installBtn.style.display = 'inline-flex';
    installBtn.innerHTML = '<span class="material-symbols-outlined">download</span><span class="aops-label">Install App</span>';

    installBtn.addEventListener('click', function () {
      if (!deferredPrompt) return;
      installBtn.style.display = 'none';
      deferredPrompt.prompt();
      deferredPrompt.userChoice.then(function (choiceResult) {
        if (choiceResult.outcome === 'accepted') {
          console.log('[PWA] User accepted the install prompt');
          deferredPrompt = null;
        } else {
          console.log('[PWA] User dismissed the install prompt');
          installBtn.style.display = 'inline-flex'; // show again if dismissed
        }
      });
    });

    // Try to append to #aops-header
    let attempts = 0;
    const interval = setInterval(function () {
      const header = document.getElementById('aops-header');
      if (header) {
        header.insertBefore(installBtn, header.firstChild);
        clearInterval(interval);
      } else if (++attempts > 50) {
        // Stop trying after 5 seconds
        clearInterval(interval);
      }
    }, 100);
  }

  // Clear prompt if already installed
  window.addEventListener('appinstalled', function () {
    console.log('[PWA] Connect Laundry Admin installed successfully.');
    if (installBtn) {
      installBtn.style.display = 'none';
    }
    deferredPrompt = null;
  });
})();
