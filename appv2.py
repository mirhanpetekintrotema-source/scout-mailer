import streamlit as st
import pandas as pd
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.application import MIMEApplication
import time
from datetime import datetime
import json
import requests
import gspread
import fitz  # PyMuPDF
import io
import matplotlib.pyplot as plt
from google.oauth2.service_account import Credentials
from ai_services import analyze_book_dna, run_matchmaker_batch, run_drafter, refine_intelligence, create_one_pager, AVAILABLE_MODELS

# --- 1. AYARLAR VE CSS ---
st.set_page_config(page_title="Scout's Pro Mailer - AI", page_icon="🛡️", layout="wide")

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Roboto:wght@300;400;500;700&display=swap');
    html, body, [class*="css"] { font-family: 'Roboto', sans-serif !important; }
    .block-container { padding-top: 1.5rem !important; max-width: 99% !important; }
    
    /* Editör Scroll Düzeltmesi */
    iframe[title="streamlit_quill.st_quill"] { min-height: 450px !important; border: 1px solid #444 !important; border-radius: 6px; background-color: #262730; overflow-y: auto !important; }
    
    /* DNA Kartları */
    .dna-container { background-color: var(--secondary-background-color); border: 1px solid #444; border-radius: 12px; padding: 20px; margin-bottom: 20px; }
    .dna-header { font-size: 12px; font-weight: 600; color: #888; margin-bottom: 5px; text-transform: uppercase; }
    .dna-value { font-size: 16px; font-weight: 500; color: var(--text-color); }
    .badge { padding: 4px 10px; border-radius: 15px; font-size: 12px; font-weight: 700; color: white; display: inline-block; margin-right: 5px; }
    .bg-red { background-color: #ff4b4b; }
    .bg-green { background-color: #00c853; }
    .bg-purple { background-color: #7c4dff; }
    
    button[kind="primary"] { background-color: #8B0000 !important; color: white !important; font-weight: bold !important; }
    .match-card { padding: 15px; border-radius: 8px; margin-bottom: 5px; border: 1px solid #444; box-shadow: 0 2px 5px rgba(0,0,0,0.2); }
</style>
""", unsafe_allow_html=True)

# --- 2. ŞİFRE VE API ---
def check_password():
    """Giriş ekranı."""
    if "password_correct" not in st.session_state: st.session_state["password_correct"] = False
    if st.session_state["password_correct"]: return True
    
    st.markdown("### 🔒 Scout's Pro Giriş")
    pwd = st.text_input("Şifre", type="password")
    if st.button("Giriş Yap"):
        try:
            if pwd == st.secrets["general"]["app_password"]:
                st.session_state["password_correct"] = True
                st.rerun()
            else: st.error("Hatalı şifre!")
        except: st.error("Secrets dosyası yapılandırılmamış!")
    return False

if not check_password(): st.stop()

# API YÜKLEME
try:
    GEMINI_API_KEY = st.secrets["api_keys"]["gemini"]
    GOOGLE_SEARCH_KEY = st.secrets["api_keys"]["google_search"]
    SEARCH_ENGINE_ID = st.secrets["api_keys"]["search_engine_id"]
    FIRECRAWL_KEY = st.secrets["api_keys"]["firecrawl"]
    GMAIL_USER = st.secrets["email"]["user"]
    GMAIL_PASS = st.secrets["email"]["pass"]
    sheets_info = st.secrets["google_sheets"]
    CREDS = Credentials.from_service_account_info(sheets_info, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    CLIENT = gspread.authorize(CREDS)
except Exception as e:
    st.error(f"⚠️ API Hatası: {str(e)}")
    st.stop()

# --- KRİTİK DEĞİŞİKLİK: ARTIK İSİM DEĞİL ID KULLANIYORUZ ---
GOOGLE_SHEET_KEY = "13a7UWJZJAd2Q5sf8Oebf98oNgeIXLCbDF9D4ESSgSqE" 
WORK_EMAIL = "mirhan.petek@introtema.com"

try: from streamlit_quill import st_quill
except ImportError: st.stop()

# STATE
default_states = {"is_sent": False, "confirm_send": False, "start_sending": False, "df_main": None, "success_log": [], "fail_log": [], "skipped_log": [], "full_report_data": [], "subject_val": "", "book_val": "", "current_sheet": None, "editor_key": 0, "email_body": "", "match_results": None, "book_dna": None, "pdf_full_text": "", "last_pdf_name": "", "intel_data": {}}
for key, val in default_states.items():
    if key not in st.session_state: st.session_state[key] = val

# --- FONKSİYONLAR ---
def get_logs_sheet():
    """
    Logs sekmesini ID ile bulur. Yoksa OTOMATİK OLUŞTURUR.
    """
    try:
        # İSİM YERİNE KEY İLE AÇIYORUZ (KESİN ÇÖZÜM)
        sh = CLIENT.open_by_key(GOOGLE_SHEET_KEY)
        try:
            return sh.worksheet("Logs")
        except:
            # Sekme yoksa oluştur
            wks = sh.add_worksheet(title="Logs", rows="1000", cols="6")
            # Başlıkları yaz
            wks.append_row(["Tarih", "Kitap", "Yayınevleri", "Hak Sahibi", "Durum", "Kaynak"])
            return wks
    except Exception as e:
        st.error(f"Google Sheet Bağlantı Hatası (ID Kontrol): {str(e)}")
        return None

def get_publisher_data():
    try:
        sh = CLIENT.open_by_key(GOOGLE_SHEET_KEY) # ID KULLANIMI
        sheet = sh.get_worksheet(0)
        raw_data = sheet.get_all_records()
        clean_data = []
        for row in raw_data:
            yayinevi_adi = str(row.get("Yayınevi Adı", "Bilinmiyor"))
            departman = str(row.get("Bu formu hangi departman/alan için dolduruyorsunuz?", "Genel"))
            blacklist = str(row.get('Yayın programınızda ASLA yer vermediğiniz, "Bize göndermeyin" dediğiniz türler veya konular var mı?', ""))
            full_profile_text = f"YAYINEVİ ID/ADI: {yayinevi_adi}\n"
            for col_name, val in row.items():
                if val and str(val).strip() and col_name not in ["Zaman damgası", "E-posta Adresi"]:
                    full_profile_text += f"- {col_name}: {val}\n"
            clean_data.append({"yayınevi": yayinevi_adi, "Departman": departman, "Blacklist": blacklist, "AI_PROFIL": full_profile_text})
        return clean_data, None
    except Exception as e: return None, str(e)

def extract_text_from_pdf(file):
    try:
        with fitz.open(stream=file.read(), filetype="pdf") as doc:
            return "".join([page.get_text() for page in doc])
    except: return None

def firecrawl_scrape(url):
    try:
        headers = {"Authorization": f"Bearer {FIRECRAWL_KEY}"}
        res = requests.post("https://api.firecrawl.dev/v0/scrape", json={"url": url, "pageOptions": {"onlyMainContent": True}}, headers=headers)
        if res.status_code == 200: return res.json().get("data", {}).get("markdown", "")
        return ""
    except: return ""

def update_master_log_cloud(kitap_adi, yay_list, mail):
    try:
        wks = get_logs_sheet()
        if wks: wks.append_row([datetime.now().strftime("%Y-%m-%d %H:%M"), kitap_adi, ", ".join(yay_list), mail, "Başarılı", "Web V2.0"])
    except: pass

def check_master_log_cloud(kitap_adi, yayinevi):
    try:
        wks = get_logs_sheet()
        if not wks: return False
        df = pd.DataFrame(wks.get_all_records())
        if df.empty: return False
        mask = (df["Kitap"] == kitap_adi) & (df["Yayınevleri"].astype(str).str.contains(yayinevi, na=False, regex=False))
        return mask.any()
    except: return False

def send_email_smtp(to_list, cc_list, subject, html_body, reply_to, attachments=None):
    try:
        msg = MIMEMultipart()
        msg["From"] = f"Mirhan Petek <{GMAIL_USER}>"
        msg["To"] = ", ".join(to_list)
        if cc_list: msg["Cc"] = ", ".join(cc_list)
        if reply_to: msg.add_header("Reply-To", reply_to)
        msg["Subject"] = subject
        msg.attach(MIMEText(f"<html><body style='font-family: Times New Roman; font-size: 14px;'>{html_body}</body></html>", "html"))
        if attachments:
            for att in attachments:
                att.seek(0)
                part = MIMEApplication(att.read(), Name=att.name)
                part["Content-Disposition"] = f'attachment; filename="{att.name}"'
                msg.attach(part)
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login(GMAIL_USER, GMAIL_PASS)
        server.sendmail(GMAIL_USER, to_list + cc_list, msg.as_string())
        server.quit()
        return True, "Başarılı"
    except Exception as e: return False, str(e)

# --- ARAYÜZ BAŞLIYOR ---
st.title("🛡️ Scout's Pro Mailer - AI (V2.0)")

# --- DASHBOARD (PATRON EKRANI) ---
with st.expander("📊 Operasyon Paneli (Dashboard)", expanded=False):
    logs_sheet = get_logs_sheet()
    if logs_sheet:
        try:
            df_logs = pd.DataFrame(logs_sheet.get_all_records())
            if not df_logs.empty:
                k1, k2, k3 = st.columns(3)
                k1.metric("Toplam Gönderim", len(df_logs))
                
                # 30 Günlük Sessizlik Kontrolü
                try:
                    last_dates = df_logs.groupby("Yayınevleri")["Tarih"].max()
                except: pass
            else:
                st.info("Henüz log kaydı yok.")
        except: st.warning("Log verisi okunamadı.")
    else:
        st.error("Google Sheets bağlantısı kurulamadı.")

col_brain, col_hands = st.columns([40, 60])

# SOL PANEL (BEYİN - AI GİRİŞİ)
with col_brain:
    st.markdown("### 🧠 Analiz Merkezi")
    
    # 1. AYARLAR
    with st.expander("⚙️ Motor Ayarları"):
        model_options = list(AVAILABLE_MODELS.keys())
        sel_dna = st.selectbox("DNA Modeli", model_options, index=0) 
        sel_match = st.selectbox("Eşleştirme Modeli", model_options, index=2) 
        sel_draft = st.selectbox("Yazar Modeli", model_options, index=1)
        MODEL_DNA = AVAILABLE_MODELS[sel_dna]
        MODEL_MATCH = AVAILABLE_MODELS[sel_match]
        MODEL_DRAFT = AVAILABLE_MODELS[sel_draft]

    # 2. GİRİŞ
    uploaded_pdf = st.file_uploader("Kitap Dosyası (PDF)", type="pdf")
    # SIFIR HATA GİRİŞİ: Sadece Link
    data_link = st.text_input("Veri Kaynağı (Link)", placeholder="Goodreads, Amazon vb. linki yapıştırın")
    # GÖRSEL KAPAK (One-Pager İçin)
    cover_img = st.file_uploader("Kapak Görseli (Opsiyonel)", type=["png", "jpg", "jpeg"])
    
    extra_notes = st.text_area("Editör Notları", height=70)

    # 3. İŞLEM BUTONLARI
    b1, b2 = st.columns(2)
    
    if uploaded_pdf:
        # PDF Değiştiyse DNA'yı yenile
        if st.session_state.last_pdf_name != uploaded_pdf.name:
            with st.spinner("DNA Çıkarılıyor (Tam Metin)..."):
                raw_text = extract_text_from_pdf(uploaded_pdf)
                if raw_text:
                    st.session_state.pdf_full_text = raw_text
                    st.session_state.book_dna = analyze_book_dna(raw_text, GEMINI_API_KEY, MODEL_DNA)
                    st.session_state.last_pdf_name = uploaded_pdf.name
    
    # --- GÖRSEL DNA KARTLARI (HİBRİT GÖRÜNÜM) ---
    if st.session_state.book_dna:
        dna = st.session_state.book_dna
        st.divider()
        st.markdown(f"""
        <div class="dna-container">
            <div style="display:flex; justify-content:space-between;">
                <div><div class="dna-header">TÜR</div><div class="dna-value">{dna.get('ana_tur', '-')}</div></div>
                <div><div class="dna-header">PITCH</div><div class="dna-value" style="color:#ffd700;">"{dna.get('pitch', '-')}"</div></div>
            </div>
            <hr style="border-color:#555;">
            <div>
                <span class="badge bg-red">🩸 {dna.get('siddet', '-')}</span>
                <span class="badge bg-purple">💡 {dna.get('dil_seviyesi', '-')}</span>
                <span class="badge bg-green">⚡ {dna.get('tempo', '-')}</span>
            </div>
        </div>
        """, unsafe_allow_html=True)
        
        # Kopyalanabilir Metin
        with st.expander("📋 Detaylı Analiz Metni (Kopyala)"):
            st.code(json.dumps(dna, indent=2, ensure_ascii=False), language="json")

    # BUTON AKSİYONLARI
    if b1.button("✍️ Email & Bülten"):
        if not st.session_state.pdf_full_text: st.error("PDF Yükleyin!")
        elif not data_link: st.error("Link Girin!")
        else:
            with st.spinner("İstihbarat toplanıyor ve yazılıyor..."):
                intel_raw = firecrawl_scrape(data_link)
                st.session_state.intel_data = refine_intelligence(intel_raw, GEMINI_API_KEY)
                
                # Mail Yaz
                res = run_drafter(
                    st.session_state.pdf_full_text,
                    extra_notes,
                    data_link, # Kitap adı yerine linki gönderiyoruz, o bulacak
                    st.session_state.intel_data,
                    st.session_state.book_dna,
                    GEMINI_API_KEY,
                    MODEL_DRAFT
                )
                st.session_state.email_body = res
                st.session_state.editor_key += 1
                st.rerun()

    if b2.button("🔍 Eşleştir"):
        if not st.session_state.book_dna: st.error("Önce PDF yükleyin!")
        else:
            pubs, _ = get_publisher_data()
            if pubs:
                # KATEGORİ FİLTRESİ (Operasyonel Hız)
                all_depts = sorted(list(set([p["Departman"] for p in pubs if p["Departman"]])))
                sel_depts = st.multiselect("Hedef Kategoriler", all_depts, default=all_depts)
                
                if st.button("Filtrele ve Başlat"): # İç içe buton sorunu olmaması için logic değişti, ama şimdilik direct run
                    pass 

    # EŞLEŞTİRME LOGIC
    if st.session_state.book_dna and st.session_state.get('start_match', False):
        pass 

# SAĞ PANEL (OPERASYON)
with col_hands:
    st.subheader("📧 Operasyon Merkezi")
    
    # ONE-PAGER İNDİRME BUTONU
    if st.session_state.book_dna and st.session_state.intel_data:
        docx_file = create_one_pager(st.session_state.book_dna, st.session_state.intel_data, cover_img)
        st.download_button(
            label="📄 Word Bültenini İndir",
            data=docx_file,
            file_name="Tanitim_Bulteni.docx",
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
    
    # Excel Yükleme
    list_file = st.file_uploader("Liste (Excel)", type="xlsx")
    
    final_list = pd.DataFrame()
    if list_file:
        xl = pd.ExcelFile(list_file)
        sheet = st.selectbox("Sayfa", xl.sheet_names)
        if sheet:
            df = pd.read_excel(list_file, sheet_name=sheet)
            cols = df.columns.tolist()
            def find_col(kws, idx):
                for k in kws: 
                    for c in cols: 
                        if k in str(c).lower(): return c
                return cols[idx] if len(cols)>idx else None
            
            yay_col = st.selectbox("Yayınevi", cols, index=cols.index(find_col(["yayinevi"],0)))
            mail_col = st.selectbox("Email", cols, index=cols.index(find_col(["mail"],1)))
            hitap_col = st.selectbox("Hitap", cols, index=cols.index(find_col(["hitap"],2)))
            
            if "Gönder?" not in df.columns: df.insert(0, "Gönder?", False)
            if st.session_state.df_main is None: st.session_state.df_main = df
            
            edited = st.data_editor(st.session_state.df_main, use_container_width=True, hide_index=True)
            st.session_state.df_main = edited
            final_list = st.session_state.df_main[st.session_state.df_main["Gönder?"]==True]

            if not final_list.empty: st.success(f"✅ {len(final_list)} alıcı seçildi.")
            else: st.warning("Alıcı seçilmedi")

    st.divider()
    # MANUEL GİRİŞLER
    email_subject = st.text_input("Konu Başlığı", value=st.session_state.subject_val)
    kitap_adi_log = st.text_input("Kitap Adı (Log İçin)", value=st.session_state.book_val)
    hak_mail = st.text_input("Hak Sahibi Email")
    
    # EDİTÖR
    quill_content = st_quill(html=True, key=f"quill_{st.session_state.editor_key}", value=st.session_state.email_body)
    if quill_content: st.session_state.email_body = quill_content
    
    if st.button("🚀 GÖNDERİMİ BAŞLAT", type="primary"):
        st.session_state.confirm_send = True

    if st.session_state.confirm_send:
        if st.button("ONAYLA VE GÖNDER"):
            st.session_state.start_sending = True
            st.session_state.confirm_send = False
            st.rerun()

    # GÖNDERİM MOTORU
    if st.session_state.start_sending:
        st.session_state.start_sending = False
        success_list = []
        progress_bar = st.progress(0)
        status_box = st.empty()
        
        for idx, row in final_list.iterrows():
            progress_bar.progress((idx + 1) / len(final_list))
            y_adi = str(row[yay_col])
            # LOG KONTROLÜ (BULUT)
            if check_master_log_cloud(kitap_adi_log, y_adi):
                status_box.warning(f"Atlandı: {y_adi}")
                continue
                
            # MAİL GÖNDER (SMTP)
            ok, msg = send_email_smtp([str(row[mail_col])], [], email_subject, st.session_state.email_body, WORK_EMAIL)
            if ok:
                success_list.append(y_adi)
                status_box.success(f"Gönderildi: {y_adi}")
            else:
                status_box.error(f"Hata ({y_adi}): {msg}")
            time.sleep(1)
            
        if success_list:
            update_master_log_cloud(kitap_adi_log, success_list, hak_mail)
            st.success("Gönderim Tamamlandı!")