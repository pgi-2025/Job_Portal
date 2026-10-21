# WhatsApp — Easy Connect (4 steps)

1. **Install & start the bridge**
   ```
   cd whatsapp-bridge
   npm install
   set WHATSAPP_API_TOKEN=mysecret123
   npm start
   ```
   A QR code appears in the terminal.

2. **Link it to WhatsApp**
   On the phone that manages your Channel: WhatsApp → Settings → Linked
   Devices → Link a Device → scan the QR code. Keep this terminal open.

3. **Get your Channel ID**
   Open your Channel → Channel info → Invite via link → copy the code
   after `whatsapp.com/channel/`. Then, in a browser, visit:
   ```
   http://localhost:4001/resolve-channel?code=YOUR_CODE
   ```
   (Add header `Authorization: Bearer mysecret123`.) Copy the `id` it
   returns.

4. **Tell the main app about it** — add these 3 lines to your `.env`:
   ```
   WHATSAPP_API_URL=http://localhost:4001/send
   WHATSAPP_API_TOKEN=mysecret123
   WHATSAPP_CHANNEL_ID=<the id from step 3>
   ```
   Restart `python app.py`. Done — new openings now post to WhatsApp
   automatically. If posting to a Channel ever fails, point it at a
   WhatsApp Group instead (same steps, just use the group's `@g.us` id).
