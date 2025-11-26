from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import sqlite3
import requests
import re

app = FastAPI(title="Dicionário de Rimas API")

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

ARQUIVO_BANCO = 'dicionario_mestre.db'

BLACKLIST = {
    "calais", "hollywood", "mcdonalds", "facebook", "youtube", 
    "google", "twitter", "instagram", "kaiser", "design", "muié"
}

# --- FUNÇÕES ---

def calcular_pontuacao(palavra_alvo, palavra_candidata, classe_candidata, origem_candidata):
    score = 0
    # Bônus Cultural
    if origem_candidata: score += 100
    # Penalidade Tamanho
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

def extrair_sufixo_visual(palavra):
    p = palavra.lower().strip()
    if p.endswith('ã'): return 'ã'
    if p.endswith('ãs'): return 'ãs'
    if p.endswith(('ão', 'ãe', 'õe')): return p[-2:]
    if p.endswith(('ãos', 'ães', 'ões')): return p[-3:]
    if p.endswith(('á', 'é', 'í', 'ó', 'ú', 'â', 'ê', 'ô')): return p[-1:] 
    if re.search(r'[aeiouáéíóúâêôãõ][rlzxnm]$', p): return p[-2:]
    vogais = "aeiouáéíóúâêô"
    for i in range(len(p) - 2, -1, -1):
        if p[i] in vogais: return p[i:]
    if len(p) >= 3: return p[-3:]
    return p 

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

        ipa, chave_perf, classe_alvo, silabas, origem_alvo = res

        # --- BUSCA UNIFICADA ---
        candidatos = []
        
        # 1. Pela Fonética
        if chave_perf:
            cursor.execute("SELECT grafia, classe, num_silabas, origem FROM palavras WHERE chave_rima = ? AND lower(grafia) != ?", (chave_perf, palavra_alvo_low))
            candidatos.extend(cursor.fetchall())
        
        # 2. Pela Visual (Garante palavras manuais)
        sufixo = extrair_sufixo_visual(palavra_alvo_low)
        if sufixo:
            cursor.execute("SELECT grafia, classe, num_silabas, origem FROM palavras WHERE grafia LIKE ? AND lower(grafia) != ? LIMIT 3000", ('%' + sufixo, palavra_alvo_low))
            candidatos.extend(cursor.fetchall())

        conn.close()

        # --- PROCESSAMENTO ---
        resultado_final = []
        vistos = set()

        for grafia, classe, n_silabas, origem in candidatos:
            g_low = grafia.lower()
            
            if len(grafia) < 2: continue
            if g_low in vistos: continue
            if g_low in BLACKLIST: continue
            if ' ' in grafia or grafia.startswith('-'): continue
            if 'Nome Próprio' in classe and not origem: continue
            
            if palavra_alvo_low.endswith(('u', 'ú')) and g_low.endswith('ou'): continue 
            if palavra_alvo_low.endswith('ou') and g_low.endswith(('u', 'ú')): continue

            vistos.add(g_low)
            score = calcular_pontuacao(palavra, grafia, classe, origem)
            
            resultado_final.append({
                "palavra": grafia, "silabas": n_silabas, "origem": origem, "score": score, "classe": classe
            })

        # Ordena: Sílabas (crescente) -> Score (decrescente) -> Alfabeto
        resultado_final.sort(key=lambda x: (x['silabas'], -x['score'], x['palavra']))

        return {
            "termo": palavra, "ipa": ipa, "classe_gramatical": classe_alvo, "origem": origem_alvo,
            "rimas": resultado_final # Lista única agora!
        }
    except Exception as e:
        print(f"ERRO: {e}")
        raise HTTPException(status_code=500, detail=str(e))