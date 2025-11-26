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

BLACKLIST = {
    "calais", "hollywood", "mcdonalds", "facebook", "youtube", 
    "google", "twitter", "instagram", "kaiser", "design", "muié"
}

# --- FUNÇÕES DE INTELIGÊNCIA FONÉTICA ---

def remover_acentos(texto):
    return ''.join(c for c in unicodedata.normalize('NFD', texto) if unicodedata.category(c) != 'Mn')

def verificar_compatibilidade_ditongo(alvo, candidato, sufixo):
    """
    Impede que vogais puras rimem com ditongos.
    Ex: Rima (I puro) vs Queima (EI ditongo).
    """
    if not sufixo: return True
    
    # Se o sufixo começa com consoante, não há risco de ditongo na junção
    if sufixo[0] not in "aeiouáéíóúâêôãõ": return True
    
    # Acha onde o sufixo começa em cada palavra
    idx_alvo = alvo.rfind(sufixo)
    idx_cand = candidato.rfind(sufixo)
    
    if idx_alvo <= 0 or idx_cand <= 0: return True
    
    # Pega a letra IMEDIATAMENTE ANTERIOR ao sufixo
    anterior_alvo = alvo[idx_alvo - 1]
    anterior_cand = candidato[idx_cand - 1]
    
    vogais = "aeiouáéíóúâêôãõ"
    
    eh_vogal_alvo = anterior_alvo in vogais
    eh_vogal_cand = anterior_cand in vogais
    
    # Se um tem vogal antes (formando ditongo) e o outro tem consoante (vogal pura) -> BLOQUEIA
    if eh_vogal_alvo != eh_vogal_cand:
        return False
        
    return True

def identificar_tonicidade(palavra):
    """
    Classifica em OXITONA, PAROXITONA ou PROPAROXITONA.
    Corrige: Rima (Parox) vs Décima (Proparox).
    """
    p = palavra.lower().strip()
    
    # 1. Verifica acentos gráficos (Definitivo para Proparoxítonas)
    acento_encontrado = -1
    for i, char in enumerate(p):
        if char in "áéíóúâêô": 
            acento_encontrado = i
            break
            
    if acento_encontrado != -1:
        # Conta vogais DEPOIS do acento
        resto = p[acento_encontrado+1:]
        num_vogais_pos = len(re.findall(r'[aeiouãõ]', resto))
        
        if num_vogais_pos == 0: return "OXITONA"
        if num_vogais_pos == 1: return "PAROXITONA"
        if num_vogais_pos >= 2: return "PROPAROXITONA"

    # 2. Sem acento gráfico (Regras Padrão)
    if p.endswith(('r', 'l', 'z', 'x', 'i', 'u', 'im', 'um', 'om', 'un')):
        return "OXITONA"
    
    return "PAROXITONA"

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

def extrair_sufixo_visual(palavra):
    p = palavra.lower().strip()
    if p.endswith('ã'): return 'ã'
    if p.endswith('ãs'): return 'ãs'
    if p.endswith(('ão', 'ãe', 'õe')): return p[-2:]
    if p.endswith(('ãos', 'ães', 'ões')): return p[-3:]
    if p.endswith(('á', 'é', 'í', 'ó', 'ú', 'â', 'ê', 'ô')): return p[-1:] 
    if re.search(r'[aeiouáéíóúâêôãõ][rlzxnm]$', p): return p[-2:]
    vogais = "aeiouáéíóúâêô"
    # Caça a vogal tônica (incluindo ditongos decrescentes)
    for i in range(len(p) - 2, -1, -1):
        if p[i] in vogais:
            # Se tiver outra vogal antes, pega ela tb (Queima -> eima)
            if i > 0 and p[i-1] in vogais: return p[i-1:]
            return p[i:]
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

        ipa_alvo, chave_perf, classe_alvo, silabas, origem_alvo = res
        
        # Análise Gramatical do Alvo
        tonicidade_alvo = identificar_tonicidade(palavra_alvo_low)
        sufixo_alvo = extrair_sufixo_visual(palavra_alvo_low)

        candidatos = []
        # 1. Fonética (Prioridade)
        if chave_perf:
            cursor.execute("SELECT grafia, classe, num_silabas, origem FROM palavras WHERE chave_rima = ? AND lower(grafia) != ?", (chave_perf, palavra_alvo_low))
            candidatos.extend(cursor.fetchall())
        
        # 2. Visual (Fallback + Complemento)
        if sufixo_alvo:
            cursor.execute("SELECT grafia, classe, num_silabas, origem FROM palavras WHERE grafia LIKE ? AND lower(grafia) != ? LIMIT 3000", ('%' + sufixo_alvo, palavra_alvo_low))
            candidatos.extend(cursor.fetchall())

        conn.close()

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

            # --- FILTRO 1: TONICIDADE (Rima vs Décima) ---
            tonicidade_cand = identificar_tonicidade(g_low)
            if tonicidade_alvo != tonicidade_cand: continue

            # --- FILTRO 2: DITONGO (Rima vs Queima) ---
            # Verifica se a estrutura anterior à rima é compatível (Vogal vs Consoante)
            if not verificar_compatibilidade_ditongo(palavra_alvo_low, g_low, sufixo_alvo):
                continue

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