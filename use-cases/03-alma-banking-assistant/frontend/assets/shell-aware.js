/* Shell-aware: hides page sidebar when inside shell iframe, proxies Alma to parent. */
(function(){
  if(!window.location.search.includes('shell=1'))return;
  var s=document.createElement('style');
  s.textContent='.nb-sidebar,#sidebar,nav.nb-sidebar{display:none!important}.nb-app{display:block!important}.nb-main{margin-left:0!important;width:100%!important}.alma-fab,.alma-panel,.alma-backdrop,#alma-widget-root{display:none!important}';
  document.head.appendChild(s);
  function proxy(){try{if(window.parent&&window.parent._almaToggle)window.parent._almaToggle()}catch(e){}}
  window.toggleAlma=proxy;
  document.addEventListener('DOMContentLoaded',function(){window.toggleAlma=proxy});
  setTimeout(function(){window.toggleAlma=proxy},500);
  setTimeout(function(){window.toggleAlma=proxy},1500);
  document.addEventListener('click',function(e){
    var link=e.target.closest('a[href^="/banking/"]');
    if(link&&window.parent!==window){e.preventDefault();window.parent.postMessage({type:'navigate',page:link.getAttribute('href')},'*')}
  });
})();