from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import sqlite3
import requests
import re
import unicodedata

app = FastAPI(title="Dicionário de Rimas API")

# Configuração de CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ARQUIVO_BANCO = 'dicionario_mestre.db'

# Lista Negra Manual (Atualizada com Anglicismos e Erros)
BLACKLIST = {
    # Lixo anterior
    "calais", "hollywood", "mcdonalds", "facebook", "youtube", 
    "google", "twitter", "instagram", "kaiser", "design", "muié",
    
    # Anglicismos terminados em ER
    "after", "boxer", "camper", "cheater", "chester", "cluster", 
    "cover", "driver", "folder", "führer", "mister", "pointer", 
    "primer", "router", "server", "teaser", "timer", "voucher", 
    "vtuber", "designer", "hamster", "hipster", "partner", 
    "sniper", "spoiler", "outlier", "best-seller", "bestseller",
    "blazer", "broder", "brother", "container", "laser", "poker",
    "poster", "pier", "scanner", "trailer", "uber", "webdesigner",
    
    # Erros ou formas arcaicas estranhas
    "fisser", "desder", "reder", "choveser", "apascentaser", 
    "desnazificacer", "arquichanceler", "aluguer", "clister",
    "bebericará", "reouver", "fizer", "disser", "trouxer", "puder" # Verbos conjugados soltos que às vezes poluem
}

# --- FUNÇÕES AUXILIARES ---

def remover_acentos(texto):
    return ''.join(c for c in unicodedata.normalize('NFD', texto) if unicodedata.category(c) != 'Mn')

def calcular_pontuacao(palavra_alvo, palavra_candidata, classe_candidata, origem_candidata):
    score = 0
    if origem_candidata: score += 100
    if len(palavra_candidata) <= 2: score -= 10
    return score

def buscar_definicao_online(palavra):
    url = "https://pt.wiktionary.org/w/api.php"
    params = {"action": "parse", "page": palavra, "prop": "text", "formatversion": "2", "format": "json", "redirects": "true"}
    headers = {'User-Agent': 'DicionarioRimasApp/1.0'}
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=5)
        data = resp.json()
        if 'error' in data: return None
        html = data.get('parse', {}).get('text', '')
        match = re.search(r'<ol>(.*?)</ol>', html, re.DOTALL)
        if match:
            item = re.search(r'<li>(.*?)</li>', match.group(1), re.DOTALL)
            if item:
                return re.sub(r'<[^>]+>', '', item.group(1)).strip().replace('\n', ' ')
    except: pass
    return None

def identificar_tonicidade(palavra, ipa=None):
    """
    Descobre se a palavra é OXITONA ou PAROXITONA.
    """
    p = palavra.lower().strip()
    
    # Se tem acento gráfico final -> OXÍTONA
    if p.endswith(('á', 'é', 'í', 'ó', 'ú', 'â', 'ê', 'ô', 'ã', 'õ', 'ão', 'ãe', 'õe')):
        return "OXITONA"
    
    # Tem acento gráfico, mas NÃO no fim?
    tem_acento = any(c in "áéíóúâêôãõ" for c in p)
    if tem_acento:
        return "PAROXITONA" 

    # SEM ACENTO GRÁFICO:
    # Termina em R, L, Z, X, I, U, IM, UM, OM -> OXÍTONA (Mulher, Barril)
    if p.endswith(('r', 'l', 'z', 'x', 'i', 'u', 'im', 'um', 'om', 'un')):
        return "OXITONA"
        
    # O Resto -> PAROXÍTONA (Casa, Zíper - nota: Zíper tem acento, mas cai na regra acima se não tiver acento no banco)
    return "PAROXITONA"

def extrair_sufixo_visual(palavra):
    p = palavra.lower().strip()
    if p.endswith('ã'): return 'ã'
    if p.endswith('ãs'): return 'ãs'
    if p.endswith(('ão', 'ãe', 'õe')): return p[-2:]
    if p.endswith(('ãos', 'ães', 'ões')): return p[-3:]
    if p.endswith(('á', 'é', 'í', 'ó', 'ú', 'â', 'ê', 'ô')): return p[-1:] 
    
    # Regra do R/L/Z (Mulher, Anel) -> Pega vogal+consoante (er, el)
    if re.search(r'[aeiouáéíóúâêôãõ][rlzxnm]$', p):
        return p[-2:]
        
    vogais = "aeiouáéíóúâêô"
    for i in range(len(p) - 2, -1, -1):
        if p[i] in vogais: return p[i:]
    if len(p) >= 3: return p[-3:]
    return p 

def extrair_vogal_tonica_ipa(ipa):
    if not ipa: return None
    ipa_limpo = ipa.replace('/', '').replace('[', '').replace(']', '').strip()
    if 'ˈ' in ipa_limpo:
        trecho_tonico = ipa_limpo.split('ˈ')[-1]
        match = re.search(r'[aeiouɛɔɐə]', trecho_tonico)
        if match: return match.group(0)
    return None

def timbres_compativeis(vogal1, vogal2):
    if not vogal1 or not vogal2: return True
    grupo_E_aberto = ['ɛ']; grupo_E_fechado = ['e']
    grupo_O_aberto = ['ɔ']; grupo_O_fechado = ['o']
    if vogal1 in grupo_E_aberto and vogal2 in grupo_E_fechado: return False
    if vogal1 in grupo_E_fechado and vogal2 in grupo_E_aberto: return False
    if vogal1 in grupo_O_aberto and vogal2 in grupo_O_fechado: return False
    if vogal1 in grupo_O_fechado and vogal2 in grupo_O_aberto: return False
    return True

# --- ROTAS ---

@app.get("/")
def home(): return {"status": "Online"}

@app.get("/definicao/{palavra}")
def obter_definicao(palavra: str):
    try:
        conn = sqlite3.connect(ARQUIVO_BANCO)
        cursor = conn.cursor()
        cursor.execute("SELECT id, grafia, classe, definicao FROM palavras WHERE lower(grafia) = ?", (palavra.lower(),))
        res = cursor.fetchone()
        
        if not res:
            conn.close()
            def_e = buscar_definicao_online(palavra)
            if def_e: return {"palavra": palavra, "classe": "?", "definicao": def_e}
            raise HTTPException(status_code=404, detail="Palavra não encontrada")

        id_p, grafia, classe, def_a = res
        if not def_a or len(def_a) < 5 or "Definição não" in def_a:
            def_on = buscar_definicao_online(grafia)
            if def_on:
                conn = sqlite3.connect(ARQUIVO_BANCO)
                conn.cursor().execute("UPDATE palavras SET definicao = ? WHERE id = ?", (def_on, id_p))
                conn.commit()
                conn.close()
                def_a = def_on
        conn.close()
        return {"palavra": grafia, "classe": classe, "definicao": def_a}
    except Exception as e: raise HTTPException(status_code=500, detail=str(e))

@app.get("/rimar/{palavra}")
def buscar_rimas(palavra: str):
    try:
        conn = sqlite3.connect(ARQUIVO_BANCO)
        cursor = conn.cursor()
        palavra_alvo_low = palavra.lower()
        
        cursor.execute("SELECT ipa, chave_rima, classe, num_silabas, origem FROM palavras WHERE lower(grafia) = ?", (palavra_alvo_low,))
        res = cursor.fetchone()
        if not res:
            conn.close()
            raise HTTPException(status_code=404, detail="Palavra não encontrada")

        ipa_alvo, chave_perf, classe_alvo, silabas, origem_alvo = res
        vogal_tonica_alvo = extrair_vogal_tonica_ipa(ipa_alvo)
        tonicidade_alvo = identificar_tonicidade(palavra_alvo_low, ipa_alvo)

        candidatos = []
        if chave_perf:
            cursor.execute("SELECT grafia, classe, num_silabas, origem, ipa FROM palavras WHERE chave_rima = ? AND lower(grafia) != ?", (chave_perf, palavra_alvo_low))
            candidatos.extend(cursor.fetchall())
        
        sufixo = extrair_sufixo_visual(palavra_alvo_low)
        if sufixo:
            cursor.execute("SELECT grafia, classe, num_silabas, origem, ipa FROM palavras WHERE grafia LIKE ? AND lower(grafia) != ? LIMIT 3000", ('%' + sufixo, palavra_alvo_low))
            candidatos.extend(cursor.fetchall())

        conn.close()

        resultado_final = []
        vistos = set()

        for grafia, classe, n_silabas, origem, ipa_cand in candidatos:
            g_low = grafia.lower()
            
            if len(grafia) < 2: continue
            if g_low in vistos: continue
            if g_low in BLACKLIST: continue
            if ' ' in grafia or grafia.startswith('-'): continue
            if 'Nome Próprio' in classe and not origem: continue
            
            # Trava U vs OU
            if palavra_alvo_low.endswith(('u', 'ú')) and g_low.endswith('ou'): continue 
            if palavra_alvo_low.endswith('ou') and g_low.endswith(('u', 'ú')): continue

            # Filtro de Tonicidade (Mulher vs Zíper)
            tonicidade_cand = identificar_tonicidade(g_low, ipa_cand)
            if tonicidade_alvo != tonicidade_cand: continue

            # Filtro de Timbre (Amor vs Maior)
            vogal_tonica_cand = extrair_vogal_tonica_ipa(ipa_cand)
            if not timbres_compativeis(vogal_tonica_alvo, vogal_tonica_cand): continue

            vistos.add(g_low)
            score = calcular_pontuacao(palavra, grafia, classe, origem)
            
            resultado_final.append({
                "palavra": grafia, "silabas": n_silabas, "origem": origem, "score": score, "classe": classe
            })

        resultado_final.sort(key=lambda x: (x['silabas'], -x['score'], x['palavra']))

        return {
            "termo": palavra, "ipa": ipa_alvo, "classe_gramatical": classe_alvo, "origem": origem_alvo,
            "rimas": resultado_final
        }
    except Exception as e:
        print(f"ERRO: {e}")
        raise HTTPException(status_code=500, detail=str(e))