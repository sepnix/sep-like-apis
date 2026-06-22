from flask import Flask, request, jsonify
import asyncio
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
import binascii
import aiohttp
import requests
import json
import like_pb2
import like_count_pb2
import uid_generator_pb2
import logging
import warnings
from urllib3.exceptions import InsecureRequestWarning
import os
import threading
import time
from datetime import datetime, timedelta
import sys
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.append("/")
from protobuf import my_pb2, output_pb2

warnings.simplefilter('ignore', InsecureRequestWarning)

app = Flask(__name__)
app.logger.setLevel(logging.INFO)

# ================= CONFIG =================
PORT = int(os.environ.get("PORT", 5000))
STORAGE_PATH = os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", ".")

ACCOUNTS_FILE = os.path.join(STORAGE_PATH, "accounts.txt")
TOKEN_DIR = os.path.join(STORAGE_PATH, "tokens")  # Sab tokens yahan save honge region wise

AES_KEY = b'Yg&tc%DEuh6%Zc^8'
AES_IV = b'6oyZDr22E3ychjM%'
TOKEN_REFRESH_INTERVAL_HOURS = 2
BATCH_SIZE = 20
MAX_WORKERS = 20

scheduler_started = False

# Ensure token directory exists
os.makedirs(TOKEN_DIR, exist_ok=True)

# ================= JWT UTILITIES =================

def decode_jwt_payload(token):
    try:
        parts = token.split('.')
        if len(parts) != 3:
            return None
        payload = parts[1]
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += '=' * padding
        decoded = base64.urlsafe_b64decode(payload)
        return json.loads(decoded)
    except:
        return None

def is_token_expired(token):
    try:
        payload = decode_jwt_payload(token)
        if not payload:
            return True
        exp = payload.get('exp')
        if not exp:
            iat = payload.get('iat')
            ttl = payload.get('ttl', 7200)
            if iat:
                exp = iat + ttl
            else:
                return True
        current_time = int(time.time())
        return current_time >= exp
    except:
        return True

def get_token_remaining_time(token):
    try:
        payload = decode_jwt_payload(token)
        if not payload:
            return 0
        exp = payload.get('exp')
        if not exp:
            iat = payload.get('iat')
            ttl = payload.get('ttl', 7200)
            if iat:
                exp = iat + ttl
            else:
                return 0
        remaining = exp - int(time.time())
        return max(0, remaining)
    except:
        return 0

def get_token_filepath(region):
    """Region ke hisaab se token file path return karega"""
    # Clean region name for filename
    region_clean = region.upper().replace(' ', '_').replace('-', '_')
    return os.path.join(TOKEN_DIR, f"token_{region_clean}.json")

# ================= JWT GENERATION =================

def get_oauth_token(password, uid):
    url = "https://ffmconnect.live.gop.garenanow.com/oauth/guest/token/grant"
    headers = {
        "User-Agent": "GarenaMSDK/4.0.19P4(G011A ;Android 9;en;US;)",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    data = {
        "uid": uid,
        "password": password,
        "response_type": "token",
        "client_type": "2",
        "client_secret": "2ee44819e9b4598845141067b281621874d0d5d7af9d8f7e00c1e54715b7d1e3",
        "client_id": "100067"
    }
    try:
        r = requests.post(url, headers=headers, data=data, timeout=30)
        j = r.json()
        token = j.get("access_token") or j.get("token") or j.get("session_key") or j.get("jwt") or (j.get("data") or {}).get("token")
        if token:
            j["access_token"] = token
        return {
            "access_token": j.get("access_token"),
            "open_id": j.get("open_id"),
            "uid": j.get("uid"),
            "raw": j
        }
    except Exception as e:
        return None

def encrypt_aes(key, iv, plaintext):
    cipher = AES.new(key, AES.MODE_CBC, iv)
    padded_message = pad(plaintext, AES.block_size)
    return cipher.encrypt(padded_message)

def parse_major_login_response(response_content):
    response_dict = {}
    try:
        lines = response_content.split("\n")
        for line in lines:
            if ":" in line:
                key, value = line.split(":", 1)
                response_dict[key.strip()] = value.strip().strip('"')
    except:
        pass
    return response_dict

def generate_jwt_token(uid, password):
    token_data = get_oauth_token(password, uid)
    if not token_data or not token_data.get("access_token"):
        return None
    
    access_token = token_data["access_token"]
    open_id = token_data.get("open_id", "")
    
    game_data = my_pb2.GameData()
    game_data.timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    game_data.game_name = "free fire"
    game_data.game_version = 1
    game_data.version_code = "1.123.1"
    game_data.os_info = "Android OS 9 / API-28 (PI/rel.cjw.20220518.114133)"
    game_data.device_type = "Handheld"
    game_data.network_provider = "Verizon Wireless"
    game_data.connection_type = "WIFI"
    game_data.screen_width = 1280
    game_data.screen_height = 960
    game_data.dpi = "240"
    game_data.cpu_info = "ARMv7 VFPv3 NEON VMH | 2400 | 4"
    game_data.total_ram = 5951
    game_data.gpu_name = "Adreno (TM) 640"
    game_data.gpu_version = "OpenGL ES 3.0"
    game_data.user_id = f"Google|{uid}-{int(time.time())}"
    game_data.ip_address = "172.190.111.97"
    game_data.language = "en"
    game_data.open_id = open_id
    game_data.access_token = access_token
    game_data.platform_type = 4
    game_data.device_form_factor = "Handheld"
    game_data.device_model = "Asus ASUS_I005DA"
    game_data.field_60 = 32968
    game_data.field_61 = 29815
    game_data.field_62 = 2479
    game_data.field_63 = 914
    game_data.field_64 = 31213
    game_data.field_65 = 32968
    game_data.field_66 = 31213
    game_data.field_67 = 32968
    game_data.field_70 = 4
    game_data.field_73 = 2
    game_data.library_path = "/data/app/com.dts.freefireth-QPvBnTUhYWE-7DMZSOGdmA==/lib/arm"
    game_data.field_76 = 1
    game_data.apk_info = "5b892aaabd688e571f688053118a162b|/data/app/com.dts.freefireth-QPvBnTUhYWE-7DMZSOGdmA==/base.apk"
    game_data.field_78 = 6
    game_data.field_79 = 1
    game_data.os_architecture = "32"
    game_data.build_number = "2019117877"
    game_data.field_85 = 1
    game_data.graphics_backend = "OpenGLES2"
    game_data.max_texture_units = 16383
    game_data.rendering_api = 4
    game_data.encoded_field_89 = "\u0017T\u0011\u0017\u0002\b\u000eUMQ\bEZ\u0003@ZK;Z\u0002\u000eV\ri[QVi\u0003\ro\t\u0007e"
    game_data.field_92 = 9204
    game_data.marketplace = "3rd_party"
    game_data.encryption_key = "KqsHT2B4It60T/65PGR5PXwFxQkVjGNi+IMCK3CFBCBfrNpSUA1dZnjaT3HcYchlIFFL1ZJOg0cnulKCPGD3C3h1eFQ="
    game_data.total_storage = 111107
    game_data.field_97 = 1
    game_data.field_98 = 1
    game_data.field_99 = "4"
    game_data.field_100 = "4"
    
    serialized = game_data.SerializeToString()
    encrypted = encrypt_aes(AES_KEY, AES_IV, serialized)
    
    url = "https://loginbp.ggblueshark.com/MajorLogin"  # Same URL for all regions
    headers = {
        'User-Agent': "Dalvik/2.1.0 (Linux; U; Android 9; ASUS_Z01QD Build/PI)",
        'Connection': "Keep-Alive",
        'Accept-Encoding': "gzip",
        'Content-Type': "application/octet-stream",
        'Expect': "100-continue",
        'X-GA': "v1 1",
        'X-Unity-Version': "2018.4.11f1",
        'ReleaseVersion': "OB53"
    }
    
    try:
        response = requests.post(url, data=encrypted, headers=headers, verify=False, timeout=30)
        if response.status_code == 200:
            parsed = output_pb2.Garena_420()
            parsed.ParseFromString(response.content)
            result = parse_major_login_response(str(parsed))
            jwt_token = result.get("token")
            region = result.get("region", "BD")  # Region from response
            if jwt_token:
                return {
                    "uid": str(uid),
                    "token": jwt_token,
                    "region": region.upper(),
                    "api": result.get("api", "N/A"),
                    "status": "live",
                    "generated_at": int(time.time())
                }
        return None
    except Exception as e:
        return None

# ================= TOKEN MANAGEMENT =================

def load_accounts():
    accounts = []
    if not os.path.exists(ACCOUNTS_FILE):
        return accounts
    with open(ACCOUNTS_FILE, "r") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" in line:
                uid, pwd = line.split(":", 1)
                accounts.append({"uid": uid.strip(), "password": pwd.strip()})
    return accounts

def load_tokens_for_region(region):
    """Specific region ke tokens load karo"""
    filepath = get_token_filepath(region)
    if not os.path.exists(filepath):
        return [], 0, 0
    
    try:
        with open(filepath, "r") as f:
            tokens = json.load(f)
        if not isinstance(tokens, list):
            return [], 0, 0
        
        valid_tokens = []
        expired_count = 0
        
        for token_entry in tokens:
            token = token_entry.get("token", "")
            if not token:
                continue
            if is_token_expired(token):
                expired_count += 1
            else:
                remaining = get_token_remaining_time(token)
                token_entry["expires_in"] = remaining
                valid_tokens.append(token_entry)
        
        return valid_tokens, expired_count, len(tokens)
    except:
        return [], 0, 0

def load_all_tokens():
    """Sabhi regions ke tokens load karo (like API ke liye)"""
    all_tokens = {}
    
    # Sabhi token files scan karo
    if os.path.exists(TOKEN_DIR):
        for filename in os.listdir(TOKEN_DIR):
            if filename.startswith("token_") and filename.endswith(".json"):
                region = filename.replace("token_", "").replace(".json", "")
                valid_tokens, _, _ = load_tokens_for_region(region)
                if valid_tokens:
                    all_tokens[region] = valid_tokens
    
    return all_tokens

def save_tokens_for_region(region, tokens):
    """Region specific tokens save karo"""
    filepath = get_token_filepath(region)
    try:
        with open(filepath, "w") as f:
            json.dump(tokens, f, indent=2)
        return True
    except Exception as e:
        app.logger.error(f"Failed to save tokens for {region}: {e}")
        return False

# ================= STARTUP TOKEN GENERATION =================

def generate_single_token(account):
    """Ek account ka token generate karo"""
    uid = account['uid']
    password = account['password']
    
    # Pehle check karo existing token valid hai kya (sabhi regions mein check karo)
    if os.path.exists(TOKEN_DIR):
        for filename in os.listdir(TOKEN_DIR):
            if filename.startswith("token_") and filename.endswith(".json"):
                filepath = os.path.join(TOKEN_DIR, filename)
                try:
                    with open(filepath, 'r') as f:
                        tokens = json.load(f)
                        for t in tokens:
                            if t.get('uid') == uid:
                                if not is_token_expired(t.get('token', '')):
                                    remaining = get_token_remaining_time(t.get('token', ''))
                                    return {
                                        'uid': uid,
                                        'token': t.get('token'),
                                        'region': t.get('region', 'BD'),
                                        'status': '✅ SKIPPED (Valid)',
                                        'expires_in': f"{remaining//3600}h {((remaining%3600)//60)}m"
                                    }
                except:
                    pass
    
    # Naya token generate karo
    for attempt in range(3):
        result = generate_jwt_token(uid, password)
        if result:
            return {
                'uid': uid,
                'token': result['token'],
                'region': result['region'],
                'status': '✅ NEW',
                'expires_in': '2h'
            }
        time.sleep(1)
    
    return {
        'uid': uid,
        'status': '❌ FAILED',
        'error': 'Invalid credentials or network issue'
    }

def generate_all_tokens_on_startup():
    """Sabhi accounts ke token generate karo - Console mein dikhega"""
    accounts = load_accounts()
    if not accounts:
        print("\n⚠️ No accounts found in accounts.txt!")
        print("📝 Please add accounts in format: uid:password\n")
        return
    
    total = len(accounts)
    print("\n" + "="*80)
    print(f"🚀 STARTING TOKEN GENERATION FOR {total} ACCOUNTS")
    print("="*80)
    print(f"📁 Storage Path: {STORAGE_PATH}")
    print(f"📂 Token Directory: {TOKEN_DIR}")
    print(f"⚙️  Batch Size: {BATCH_SIZE} | Parallel Workers: {MAX_WORKERS}")
    print("="*80 + "\n")
    
    results = {'success': [], 'failed': [], 'skipped': []}
    batches = [accounts[i:i+BATCH_SIZE] for i in range(0, total, BATCH_SIZE)]
    
    start_time = time.time()
    
    for batch_num, batch in enumerate(batches, 1):
        print(f"\n📦 BATCH {batch_num}/{len(batches)} - Processing {len(batch)} accounts...")
        print("-"*60)
        
        batch_start = time.time()
        batch_results = []
        
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            future_to_account = {executor.submit(generate_single_token, account): account for account in batch}
            
            for future in as_completed(future_to_account):
                result = future.result()
                batch_results.append(result)
                if result['status'].startswith('✅ NEW'):
                    print(f"  {result['status']} | UID: {result['uid']:15} | Region: {result['region']:6} | Expires: {result['expires_in']}")
                elif result['status'].startswith('✅ SKIPPED'):
                    print(f"  {result['status']} | UID: {result['uid']:15} | Region: {result['region']:6} | Expires: {result['expires_in']}")
                else:
                    print(f"  {result['status']} | UID: {result['uid']:15} | Error: {result.get('error', 'Unknown')}")
        
        # Batch results ko categorize karo
        for r in batch_results:
            if r['status'].startswith('✅ NEW'):
                results['success'].append(r)
            elif r['status'].startswith('✅ SKIPPED'):
                results['skipped'].append(r)
            else:
                results['failed'].append(r)
        
        batch_time = time.time() - batch_start
        print(f"\n⏱️  Batch {batch_num} completed in {batch_time:.1f}s")
        
        # Har batch ke baad save karo (region wise)
        save_all_tokens_by_region(results['success'] + results['skipped'])
        
        # Progress show
        processed = min(batch_num * BATCH_SIZE, total)
        print(f"📊 Progress: {processed}/{total} | ✅ New: {len(results['success'])} | ⏭️ Skipped: {len(results['skipped'])} | ❌ Failed: {len(results['failed'])}")
        
        if batch_num < len(batches):
            time.sleep(1)
    
    # Final Summary
    total_time = time.time() - start_time
    print("\n" + "="*80)
    print("🎉 TOKEN GENERATION COMPLETE!")
    print("="*80)
    print(f"⏱️  Total Time: {total_time:.1f} seconds")
    print(f"📊 Total Accounts: {total}")
    print(f"✅ New Generated: {len(results['success'])}")
    print(f"⏭️ Already Valid: {len(results['skipped'])}")
    print(f"❌ Failed: {len(results['failed'])}")
    print(f"📈 Success Rate: {((len(results['success'])+len(results['skipped']))/total*100):.1f}%")
    
    if results['failed']:
        print("\n❌ FAILED ACCOUNTS LIST:")
        print("-"*40)
        for fail in results['failed']:
            print(f"   UID: {fail['uid']}")
        
        failed_file = os.path.join(STORAGE_PATH, "failed_accounts.txt")
        with open(failed_file, 'w') as f:
            for fail in results['failed']:
                f.write(f"{fail['uid']}\n")
        print(f"\n📝 Failed UIDs saved to: {failed_file}")
    
    # Show region wise stats
    print("\n📁 REGION WISE TOKEN FILES SAVED:")
    print("-"*40)
    if os.path.exists(TOKEN_DIR):
        for filename in sorted(os.listdir(TOKEN_DIR)):
            if filename.startswith("token_") and filename.endswith(".json"):
                filepath = os.path.join(TOKEN_DIR, filename)
                with open(filepath, 'r') as f:
                    tokens = json.load(f)
                    valid_count = 0
                    for t in tokens:
                        if not is_token_expired(t.get('token', '')):
                            valid_count += 1
                    print(f"   📄 {filename} -> {len(tokens)} tokens ({valid_count} valid)")
    
    print("\n" + "="*80 + "\n")
    return results

def save_all_tokens_by_region(tokens_list):
    """Region wise tokens save karo - Har unique region ke liye alag JSON"""
    region_data = {}
    
    for token in tokens_list:
        if token.get('status', '').startswith('❌'):
            continue
        
        region = token.get("region", "BD").upper()
        
        if region not in region_data:
            region_data[region] = []
        
        clean_token = {k: v for k, v in token.items() if k not in ['status', 'expires_in', 'error']}
        region_data[region].append(clean_token)
    
    # Save each region's tokens
    for region, data in region_data.items():
        filepath = get_token_filepath(region)
        
        # Load existing tokens for this region
        existing = []
        if os.path.exists(filepath):
            try:
                with open(filepath, 'r') as f:
                    existing = json.load(f)
            except:
                pass
        
        # Merge based on UID
        uid_map = {item["uid"]: item for item in existing}
        for new_item in data:
            uid_map[new_item["uid"]] = new_item
        
        save_tokens_for_region(region, list(uid_map.values()))

def refresh_expired_tokens_for_region(region):
    """Sirf expired tokens refresh karo for specific region"""
    accounts = load_accounts()
    if not accounts:
        return False
    
    filepath = get_token_filepath(region)
    
    current_tokens = []
    if os.path.exists(filepath):
        try:
            with open(filepath, "r") as f:
                current_tokens = json.load(f)
        except:
            current_tokens = []
    
    uid_to_account = {acc["uid"]: acc for acc in accounts}
    tokens_to_refresh = []
    
    for token_entry in current_tokens:
        uid = token_entry.get("uid")
        token = token_entry.get("token", "")
        if not token or is_token_expired(token):
            if uid in uid_to_account:
                tokens_to_refresh.append(uid_to_account[uid])
    
    existing_uids = {t.get("uid") for t in current_tokens}
    for acc in accounts:
        if acc["uid"] not in existing_uids:
            tokens_to_refresh.append(acc)
    
    if not tokens_to_refresh:
        return True
    
    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(generate_jwt_token, acc['uid'], acc['password']) for acc in tokens_to_refresh]
        for future in as_completed(futures):
            result = future.result()
            if result and result.get('region', '').upper() == region.upper():
                results.append(result)
    
    if results:
        valid_existing = [t for t in current_tokens if not is_token_expired(t.get("token", ""))]
        uid_map = {t["uid"]: t for t in valid_existing}
        for new_token in results:
            uid_map[new_token["uid"]] = new_token
        return save_tokens_for_region(region, list(uid_map.values()))
    
    return False

def scheduled_refresh():
    """Background scheduler - Sabhi regions ke liye"""
    while True:
        next_run = datetime.now() + timedelta(hours=TOKEN_REFRESH_INTERVAL_HOURS)
        app.logger.info(f"🔄 Scheduled refresh at: {next_run}")
        
        # Sabhi regions ke liye refresh karo
        if os.path.exists(TOKEN_DIR):
            for filename in os.listdir(TOKEN_DIR):
                if filename.startswith("token_") and filename.endswith(".json"):
                    region = filename.replace("token_", "").replace(".json", "")
                    app.logger.info(f"🔄 Refreshing tokens for region: {region}")
                    refresh_expired_tokens_for_region(region)
        
        time.sleep(TOKEN_REFRESH_INTERVAL_HOURS * 3600)

def start_scheduler():
    global scheduler_started
    if not scheduler_started:
        t = threading.Thread(target=scheduled_refresh, daemon=True)
        t.start()
        scheduler_started = True
        app.logger.info("✅ Token scheduler started in background")

# ================= LIKE API =================

def get_like_url(server_name):
    """Server name ke hisaab se like URL return karega"""
    if server_name == "IND":
        return "https://client.ind.freefiremobile.com/LikeProfile"
    else:
        # BD, ME, NA, BR, SAC, etc. sab ke liye same URL
        return "https://clientbp.ggpolarbear.com/LikeProfile"

def get_player_info_url(server_name):
    """Server name ke hisaab se player info URL return karega"""
    if server_name == "IND":
        return "https://client.ind.freefiremobile.com/GetPlayerPersonalShow"
    else:
        # BD, ME, NA, BR, SAC, etc. sab ke liye same URL
        return "https://clientbp.ggpolarbear.com/GetPlayerPersonalShow"

def encrypt_for_like(plaintext):
    try:
        cipher = AES.new(AES_KEY, AES.MODE_CBC, AES_IV)
        padded = pad(plaintext, AES.block_size)
        encrypted = cipher.encrypt(padded)
        return binascii.hexlify(encrypted).decode('utf-8')
    except:
        return None

def create_like_protobuf(uid, region):
    try:
        msg = like_pb2.like()
        msg.uid = int(uid)
        msg.region = region
        return msg.SerializeToString()
    except:
        return None

def create_uid_protobuf(uid):
    try:
        msg = uid_generator_pb2.uid_generator()
        msg.saturn_ = int(uid)
        msg.garena = 1
        return msg.SerializeToString()
    except:
        return None

def decode_player_info(binary_data):
    if not binary_data or len(binary_data) < 5:
        return None
    try:
        info = like_count_pb2.Info()
        info.ParseFromString(binary_data)
        if info.AccountInfo.UID != 0:
            return info
    except:
        pass
    try:
        basic = like_count_pb2.BasicInfo()
        basic.ParseFromString(binary_data)
        if basic.UID != 0:
            info = like_count_pb2.Info()
            info.AccountInfo.UID = basic.UID
            info.AccountInfo.PlayerNickname = basic.PlayerNickname
            info.AccountInfo.Likes = basic.Likes
            return info
    except:
        pass
    return None

def get_player_info(encrypted_uid, server_name, token):
    try:
        url = get_player_info_url(server_name)
        
        edata = bytes.fromhex(encrypted_uid)
        headers = {
            'User-Agent': "Dalvik/2.1.0 (Linux; U; Android 9; ASUS_Z01QD Build/PI)",
            'Connection': "Keep-Alive",
            'Accept-Encoding': "gzip",
            'Authorization': f"Bearer {token}",
            'Content-Type': "application/x-www-form-urlencoded",
            'Expect': "100-continue",
            'X-Unity-Version': "2018.4.11f1",
            'X-GA': "v1 1",
            'ReleaseVersion': "OB53"
        }
        
        response = requests.post(url, data=edata, headers=headers, verify=False, timeout=15)
        
        if response.status_code == 401:
            return None, "EXPIRED"
        if response.status_code != 200:
            return None, f"HTTP_{response.status_code}"
        
        result = decode_player_info(response.content)
        return result, "OK"
    except:
        return None, "ERROR"

async def send_like_request(encrypted_uid, token, url):
    try:
        edata = bytes.fromhex(encrypted_uid)
        headers = {
            'User-Agent': "Dalvik/2.1.0 (Linux; U; Android 9; ASUS_Z01QD Build/PI)",
            'Connection': "Keep-Alive",
            'Accept-Encoding': "gzip",
            'Authorization': f"Bearer {token}",
            'Content-Type': "application/x-www-form-urlencoded",
            'Expect': "100-continue",
            'X-Unity-Version': "2018.4.11f1",
            'X-GA': "v1 1",
            'ReleaseVersion': "OB53"
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=edata, headers=headers, timeout=10) as resp:
                return resp.status
    except:
        return None

async def send_multiple_likes(uid, server_name, tokens):
    try:
        proto_data = create_like_protobuf(uid, server_name)
        if not proto_data:
            return None
        
        encrypted = encrypt_for_like(proto_data)
        if not encrypted:
            return None
        
        url = get_like_url(server_name)
        
        tasks = []
        for i in range(100):
            token = tokens[i % len(tokens)]["token"]
            tasks.append(send_like_request(encrypted, token, url))
        
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return results
    except:
        return None

# ================= FLASK ROUTES =================

@app.route('/')
def home():
    return jsonify({
        "status": "running",
        "service": "Free Fire Like API",
        "timestamp": datetime.now().isoformat()
    })

@app.route('/health')
def health():
    return jsonify({"status": "healthy"}), 200

@app.route('/token_status', methods=['GET'])
def api_token_status():
    server_name = request.args.get("server_name", "").upper()
    
    if not server_name:
        # Sabhi regions ka status dikhao
        all_status = {}
        if os.path.exists(TOKEN_DIR):
            for filename in os.listdir(TOKEN_DIR):
                if filename.startswith("token_") and filename.endswith(".json"):
                    region = filename.replace("token_", "").replace(".json", "")
                    valid_tokens, expired_count, total_count = load_tokens_for_region(region)
                    all_status[region] = {
                        "total_tokens": total_count,
                        "valid_tokens": len(valid_tokens),
                        "expired_tokens": expired_count
                    }
        return jsonify({"all_regions": all_status})
    else:
        valid_tokens, expired_count, total_count = load_tokens_for_region(server_name)
        return jsonify({
            "server": server_name,
            "total_tokens": total_count,
            "valid_tokens": len(valid_tokens),
            "expired_tokens": expired_count
        })

@app.route('/regions', methods=['GET'])
def list_regions():
    """Available regions dikhao"""
    regions = []
    if os.path.exists(TOKEN_DIR):
        for filename in os.listdir(TOKEN_DIR):
            if filename.startswith("token_") and filename.endswith(".json"):
                region = filename.replace("token_", "").replace(".json", "")
                regions.append(region)
    return jsonify({"available_regions": regions})

@app.route('/like', methods=['GET'])
def handle_like():
    uid = request.args.get("uid")
    server_name = request.args.get("server_name", "").upper()
    
    if not uid or not server_name:
        return jsonify({"error": "Missing uid or server_name"}), 400
    
    try:
        # Load tokens for this specific region
        valid_tokens, expired_count, total_count = load_tokens_for_region(server_name)
        
        # If no valid tokens, try to refresh
        if not valid_tokens or len(valid_tokens) == 0:
            app.logger.warning(f"No valid tokens for {server_name}, attempting refresh...")
            refresh_expired_tokens_for_region(server_name)
            valid_tokens, expired_count, total_count = load_tokens_for_region(server_name)
            
            if not valid_tokens or len(valid_tokens) == 0:
                return jsonify({
                    "error": f"No valid tokens available for region {server_name}",
                    "message": "Token refresh attempted but failed"
                }), 500
        
        check_token = valid_tokens[0]["token"]
        
        uid_proto = create_uid_protobuf(uid)
        if not uid_proto:
            return jsonify({"error": "UID protobuf failed"}), 500
        
        encrypted_uid = encrypt_for_like(uid_proto)
        if not encrypted_uid:
            return jsonify({"error": "Encryption failed"}), 500
        
        before_info, status = get_player_info(encrypted_uid, server_name, check_token)
        
        if status == "EXPIRED":
            refresh_expired_tokens_for_region(server_name)
            valid_tokens, _, _ = load_tokens_for_region(server_name)
            if not valid_tokens:
                return jsonify({"error": "Token expired and refresh failed"}), 500
            check_token = valid_tokens[0]["token"]
            before_info, status = get_player_info(encrypted_uid, server_name, check_token)
        
        if before_info is None:
            return jsonify({"error": "Failed to retrieve player info"}), 500
        
        before_likes = int(before_info.AccountInfo.Likes)
        player_name = str(before_info.AccountInfo.PlayerNickname)
        player_uid = int(before_info.AccountInfo.UID)
        
        # Send likes
        await_result = asyncio.run(send_multiple_likes(uid, server_name, valid_tokens))
        time.sleep(2)
        
        after_info, _ = get_player_info(encrypted_uid, server_name, check_token)
        
        if after_info is None:
            return jsonify({"error": "Failed to get final player info"}), 500
        
        after_likes = int(after_info.AccountInfo.Likes)
        likes_given = after_likes - before_likes
        
        return jsonify({
            "PlayerNickname": player_name,
            "UID": player_uid,
            "LikesbeforeCommand": before_likes,
            "LikesafterCommand": after_likes,
            "LikesGivenByAPI": likes_given,
            "status": 1 if likes_given > 0 else 2,
            "tokens_used": len(valid_tokens),
            "region_used": server_name
        })
        
    except Exception as e:
        app.logger.error(f"Like handler error: {e}")
        return jsonify({"error": str(e)}), 500

# ================= MAIN =================

if __name__ == '__main__':
    # Create accounts file if not exists
    if not os.path.exists(ACCOUNTS_FILE):
        with open(ACCOUNTS_FILE, 'w') as f:
            f.write("# Format: uid:password\n")
            f.write("# Example: 123456789:mypassword\n")
        print(f"\n📝 Created {ACCOUNTS_FILE}")
        print("⚠️ Please add your accounts and restart the app!\n")
    else:
        # 🔥 APP START HONE SE PEHLE TOKEN GENERATE HONGE 🔥
        generate_all_tokens_on_startup()
    
    # Start background scheduler
    start_scheduler()
    
    # Run Flask app
    print(f"\n🚀 Starting Flask server on port {PORT}...")
    print(f"\n📡 API Endpoints:")
    print(f"   GET /like?uid=XXXX&server_name=IND - Send likes to IND player")
    print(f"   GET /like?uid=XXXX&server_name=BD  - Send likes to BD player")
    print(f"   GET /like?uid=XXXX&server_name=ME  - Send likes to ME player")
    print(f"   GET /like?uid=XXXX&server_name=NA  - Send likes to NA player")
    print(f"   GET /token_status?server_name=IND   - Check token status")
    print(f"   GET /regions                         - List all regions")
    print(f"   GET /health                          - Health check\n")
    
    app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False, threaded=True)