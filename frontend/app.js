let idToken = null;
const sessionId = crypto.randomUUID();

async function login() {
  const username = document.getElementById("username").value;
  const password = document.getElementById("password").value;
  const resp = await fetch("/api/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!resp.ok) {
    document.getElementById("login-error").textContent = "Login failed";
    return;
  }
  const data = await resp.json();
  idToken = data.id_token;
  document.getElementById("login-view").style.display = "none";
  document.getElementById("chat-view").style.display = "block";
}

async function sendMessage() {
    const input = document.getElementById("message-input");
    const text = input.value;
    input.value = "";
    appendMessage("You", text);
  
    const resp = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json", "Authorization": `Bearer ${idToken}` },
      body: JSON.stringify({ session_id: sessionId, message: text }),
    });
    const data = await resp.json();
  
    if (data.tool_calls && data.tool_calls.length > 0) {
      const names = data.tool_calls.map(tc => tc.name.replace("mcp__aig_research__", "")).join(", ");
      appendToolInfo(`🔧 used tools: ${names}`);
    }
    appendMessage("Assistant", data.reply);
  }
  
  function appendToolInfo(text) {
    const div = document.createElement("div");
    div.className = "tool-info";
    div.textContent = text;
    document.getElementById("messages").appendChild(div);
  }

function appendMessage(sender, text) {
  const div = document.createElement("div");
  div.textContent = `${sender}: ${text}`;
  document.getElementById("messages").appendChild(div);
}