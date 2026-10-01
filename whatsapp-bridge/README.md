# WhatsApp Channel Bridge (free, best-effort)

Posts new-openings messages to your real WhatsApp Channel for free, using
Baileys (open-source, unofficial). Posting to Channels isn't officially
supported by WhatsApp's automation tools, so this may not work 100% of
the time — if it stops working, fall back to copy-pasting manually or a
paid provider (Whapi.Cloud etc.), no other code changes needed either way.

## 1. Install
```
cd whatsapp-bridge
npm install
```

## 2. Set a shared secret
Pick any random password, e.g. `mysecret123`, and put it in **two** places:
- This folder's environment when you run it (see step 3)
- Your main `.env` file as `WHATSAPP_API_TOKEN=mysecret123`

## 3. Run it and link WhatsApp
```
set WHATSAPP_API_TOKEN=mysecret123   (Windows PowerShell: $env:WHATSAPP_API_TOKEN="mysecret123")
npm start
```
A QR code prints in the terminal. On the phone that **administers your
Channel**, open WhatsApp → Settings → Linked Devices → Link a Device →
scan it. Leave this terminal window running — it needs to stay open
(or run on a small always-on machine) to keep sending messages.

## 4. Get your Channel's ID
Open your Channel in WhatsApp → Channel info → Invite via link. Copy the
code after `https://whatsapp.com/channel/`. Then visit in a browser
(while the bridge is running):
```
http://localhost:4001/resolve-channel?code=YOUR_CODE
```
with header `Authorization: Bearer mysecret123` (use a tool like Postman,
or curl:
```
curl "http://localhost:4001/resolve-channel?code=YOUR_CODE" -H "Authorization: Bearer mysecret123"
```
Copy the `id` field it returns (looks like `120363xxxxxxxxxx@newsletter`).

## 5. Point the main app at the bridge
In your main `.env`:
```
WHATSAPP_API_URL=http://localhost:4001/send
WHATSAPP_API_TOKEN=mysecret123
WHATSAPP_CHANNEL_ID=120363xxxxxxxxxx@newsletter
```
Restart `python app.py`. Now, whenever a company saves open roles, the
Flask backend calls this bridge, and the bridge tries to post to your
Channel — for free.

## If sending silently fails
Mainline Baileys has known limits posting to Channels (as opposed to
Groups, which it handles very reliably). If `/send` keeps erroring:
- Check this terminal's logs for the exact error.
- Consider moving your 100 students to a WhatsApp **Group** instead —
  the same bridge code works great for groups, just send to the group's
  JID (ending in `@g.us`) instead of `@newsletter`.
- Or switch `WHATSAPP_API_URL`/`WHATSAPP_API_TOKEN` in `.env` to a paid
  provider like Whapi.Cloud, which explicitly supports Channels.
